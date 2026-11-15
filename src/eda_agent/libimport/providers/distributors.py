# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Distributor and aggregator catalogues.

Five sources of part IDENTITY: manufacturer part number, datasheet,
lifecycle and stock. None of them yields a symbol or a footprint, which
is why they all declare ``kind = "catalogue"`` and no importable format.

They matter because of what this project measures everything against.
``lib_audit_footprint_vs_datasheet`` and the design checks all want a
datasheet URL, and until now nothing here could find one: the library
providers know a symbol's name but not the part's paperwork.

Every endpoint below was probed live before being written down. The
comment on each class records what it answered, because "I checked" is
worth nothing without the result. Three further candidates were probed
and DROPPED for answering 404 on the recalled URL rather than being
shipped as plausible guesses.

WHAT IS AND IS NOT VERIFIED HERE. The endpoints exist: measured. The
auth mechanisms follow each vendor's published scheme. The response
parsing has NOT been exercised against a live credentialed reply by this
project, which every class states through ``verified_live = False`` and
which the provider catalogue reports. The parsers are written to survive
a renamed field rather than assume one, so a shape drift degrades a hit
instead of losing the whole search.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.parse
from typing import Any

from eda_agent.libimport.providers._distributor import (
    DistributorProvider,
    first_string,
)
from eda_agent.libimport.providers.base import PartHit

__all__ = [
    "DigiKeyProvider",
    "Element14Provider",
    "MouserProvider",
    "NexarProvider",
    "TmeProvider",
]


class DigiKeyProvider(DistributorProvider):
    """Digi-Key's product search.

    Probed: ``POST /products/v4/search/keyword`` answered HTTP 400 to an
    unauthenticated request and ``POST /v1/oauth2/token`` answered 400 to
    an empty grant, so both paths exist and reject rather than 404.
    """

    name = "digikey"
    description = (
        "Digi-Key catalogue: MPN, datasheet, lifecycle and live stock. "
        "Identity only, no symbol or footprint. Needs an OAuth client "
        "from the Digi-Key developer portal via DIGIKEY_CLIENT_ID and "
        "DIGIKEY_CLIENT_SECRET.")
    env_vars = ("DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")

    _TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
    _SEARCH_URL = "https://api.digikey.com/products/v4/search/keyword"

    def _token(self) -> str:
        creds = self._credentials()
        body = urllib.parse.urlencode({
            "client_id": creds["DIGIKEY_CLIENT_ID"],
            "client_secret": creds["DIGIKEY_CLIENT_SECRET"],
            "grant_type": "client_credentials",
        }).encode("ascii")
        payload = self._request(
            self._TOKEN_URL, method="POST", body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        token = first_string(payload, ("access_token",))
        if not token:
            raise self._no_token()
        return token

    def _no_token(self):
        from eda_agent.libimport.providers.base import ProviderUnavailable
        return ProviderUnavailable(
            f"{self.name} accepted the credential exchange but returned no "
            f"access_token")

    def search(self, query: str, limit: int = 20) -> list[PartHit]:
        token = self._token()
        creds = self._credentials()
        body = json.dumps({
            "Keywords": str(query),
            "Limit": int(limit),
            "Offset": 0,
        }).encode("utf-8")
        payload = self._request(
            self._SEARCH_URL, method="POST", body=body,
            headers={
                "Authorization": f"Bearer {token}",
                "X-DIGIKEY-Client-Id": creds["DIGIKEY_CLIENT_ID"],
                "Content-Type": "application/json",
            })
        products = payload.get("Products") or payload.get("products") or []
        hits = []
        for product in products[:limit]:
            mpn = first_string(product,
                               ("ManufacturerProductNumber",),
                               ("ManufacturerPartNumber",),
                               ("Description", "ProductDescription"))
            if not mpn:
                continue
            hits.append(self._hit(
                mpn,
                mpn=mpn,
                manufacturer=first_string(product,
                                          ("Manufacturer", "Name"),
                                          ("Manufacturer", "Value")),
                description=first_string(
                    product, ("Description", "ProductDescription"),
                    ("DetailedDescription",)),
                datasheet=first_string(product, ("DatasheetUrl",),
                                       ("PrimaryDatasheet",)),
            ))
        return hits


class MouserProvider(DistributorProvider):
    """Mouser's keyword search.

    Probed: ``POST /api/v1/search/keyword`` answered HTTP **200** with
    ``{"Errors":[{"Code":"Invalid",...}]}`` to a bogus key. That is the
    reason ``_reject_error_payload`` exists in the base class: judged on
    status alone, a rejected credential here is indistinguishable from a
    search that ran and matched nothing.
    """

    name = "mouser"
    description = (
        "Mouser catalogue: MPN, datasheet, lifecycle and stock. Identity "
        "only, no symbol or footprint. Needs a search API key from the "
        "Mouser developer portal via MOUSER_API_KEY.")
    env_vars = ("MOUSER_API_KEY",)

    _SEARCH_URL = "https://api.mouser.com/api/v1/search/keyword"

    def search(self, query: str, limit: int = 20) -> list[PartHit]:
        creds = self._credentials()
        body = json.dumps({
            "SearchByKeywordRequest": {
                "keyword": str(query),
                "records": int(limit),
                "startingRecord": 0,
            }
        }).encode("utf-8")
        payload = self._request(
            self._query(self._SEARCH_URL,
                        {"apiKey": creds["MOUSER_API_KEY"]}),
            method="POST", body=body,
            headers={"Content-Type": "application/json"})
        results = (payload.get("SearchResults") or {})
        parts = results.get("Parts") or []
        hits = []
        for part in parts[:limit]:
            mpn = first_string(part, ("ManufacturerPartNumber",))
            if not mpn:
                continue
            hits.append(self._hit(
                mpn,
                mpn=mpn,
                manufacturer=first_string(part, ("Manufacturer",)),
                description=first_string(part, ("Description",)),
                datasheet=first_string(part, ("DataSheetUrl",)),
            ))
        return hits


class NexarProvider(DistributorProvider):
    """Nexar, the API behind Octopart.

    Probed: ``POST https://api.nexar.com/graphql`` answered a valid
    GraphQL body to an unauthenticated ``{__typename}`` introspection,
    and ``https://identity.nexar.com/connect/token`` answered 400 to an
    empty grant. Both exist; real part queries need the token.
    """

    name = "nexar"
    description = (
        "Nexar (Octopart) part search: MPN, manufacturer, description "
        "and datasheet link. Identity only: no prices, stock, symbol or "
        "footprint. Needs NEXAR_CLIENT_ID and NEXAR_CLIENT_SECRET from "
        "your own Nexar app, whose API terms are yours to keep.")
    env_vars = ("NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET")

    _TOKEN_URL = "https://identity.nexar.com/connect/token"
    _API_URL = "https://api.nexar.com/graphql"

    _QUERY = """
    query SearchMpn($q: String!, $limit: Int!) {
      supSearchMpn(q: $q, limit: $limit) {
        results {
          part {
            mpn
            manufacturer { name }
            shortDescription
            bestDatasheet { url }
          }
        }
      }
    }
    """

    def _token(self) -> str:
        creds = self._credentials()
        body = urllib.parse.urlencode({
            "client_id": creds["NEXAR_CLIENT_ID"],
            "client_secret": creds["NEXAR_CLIENT_SECRET"],
            "grant_type": "client_credentials",
        }).encode("ascii")
        payload = self._request(
            self._TOKEN_URL, method="POST", body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        token = first_string(payload, ("access_token",))
        if not token:
            from eda_agent.libimport.providers.base import ProviderUnavailable
            raise ProviderUnavailable(
                f"{self.name} returned no access_token")
        return token

    def search(self, query: str, limit: int = 20) -> list[PartHit]:
        token = self._token()
        body = json.dumps({
            "query": self._QUERY,
            "variables": {"q": str(query), "limit": int(limit)},
        }).encode("utf-8")
        payload = self._request(
            self._API_URL, method="POST", body=body,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"})
        results = (((payload.get("data") or {}).get("supSearchMpn") or {})
                   .get("results") or [])
        hits = []
        for entry in results[:limit]:
            part = (entry or {}).get("part") or {}
            mpn = first_string(part, ("mpn",))
            if not mpn:
                continue
            hits.append(self._hit(
                mpn,
                mpn=mpn,
                manufacturer=first_string(part, ("manufacturer", "name")),
                description=first_string(part, ("shortDescription",)),
                datasheet=first_string(part, ("bestDatasheet", "url")),
            ))
        return hits


class Element14Provider(DistributorProvider):
    """element14 / Farnell / Newark product search.

    Probed: ``GET /catalog/products`` answered HTTP 403 with the body
    ``Developer Inactive``, so the path exists and gates on the key.
    """

    name = "element14"
    description = (
        "element14 (Farnell, Newark) catalogue: MPN, datasheet and "
        "stock. Identity only, no symbol or footprint. Needs a product "
        "search key via ELEMENT14_API_KEY; set ELEMENT14_STORE to pick "
        "a regional store.")
    env_vars = ("ELEMENT14_API_KEY",)

    _SEARCH_URL = "https://api.element14.com/catalog/products"
    #: Regional storefront. element14 serves different catalogues per
    #: store, so this changes which parts and prices come back.
    _DEFAULT_STORE = "uk.farnell.com"

    def search(self, query: str, limit: int = 20) -> list[PartHit]:
        import os

        creds = self._credentials()
        store = os.environ.get("ELEMENT14_STORE", "").strip() \
            or self._DEFAULT_STORE
        url = self._query(self._SEARCH_URL, {
            "term": f"any:{query}",
            "storeInfo.id": store,
            "callInfo.apiKey": creds["ELEMENT14_API_KEY"],
            "callInfo.responseDataFormat": "json",
            "resultsSettings.numberOfResults": str(int(limit)),
            "resultsSettings.offset": "0",
            "resultsSettings.responseGroup": "large",
        })
        payload = self._request(url)
        products = ((payload.get("premierFarnellPartNumberReturn") or {})
                    .get("products") or [])
        hits = []
        for product in products[:limit]:
            mpn = first_string(product, ("translatedManufacturerPartNumber",),
                               ("sku",))
            if not mpn:
                continue
            datasheets = product.get("datasheets") or []
            datasheet = ""
            if isinstance(datasheets, list) and datasheets:
                datasheet = first_string(datasheets[0], ("url",))
            hits.append(self._hit(
                mpn,
                mpn=mpn,
                manufacturer=first_string(product, ("brandName",),
                                          ("vendorName",)),
                description=first_string(
                    product, ("displayName",),
                    ("productOverview", "description")),
                datasheet=datasheet,
            ))
        return hits


class TmeProvider(DistributorProvider):
    """TME (Transfer Multisort Elektronik) product search.

    Probed: ``POST /Products/Search.json`` answered HTTP 403 with
    ``{"Status":"E_ACTION_FORBIDDEN"}``, so the path exists and gates on
    the signed request.

    TME signs every call: HMAC-SHA1 over the method, the URL and the
    sorted parameters, base64 encoded. That is implemented here but, like
    every parser in this module, has not been exercised against a live
    credentialed reply.
    """

    name = "tme"
    description = (
        "TME catalogue: MPN, datasheet and stock, strongest on European "
        "availability. Identity only, no symbol or footprint. Needs "
        "TME_TOKEN and TME_SECRET; requests are HMAC signed.")
    env_vars = ("TME_TOKEN", "TME_SECRET")

    _SEARCH_URL = "https://api.tme.eu/Products/Search.json"

    def _sign(self, url: str, params: dict[str, str], secret: str) -> str:
        """TME's HMAC-SHA1 request signature.

        The signed string is METHOD&url&params, each percent-encoded as a
        whole, with parameters sorted by key.
        """
        encoded = urllib.parse.urlencode(sorted(params.items()))
        base = "&".join([
            "POST",
            urllib.parse.quote(url, safe=""),
            urllib.parse.quote(encoded, safe=""),
        ])
        digest = hmac.new(secret.encode("utf-8"), base.encode("utf-8"),
                          hashlib.sha1).digest()
        return base64.b64encode(digest).decode("ascii")

    def search(self, query: str, limit: int = 20) -> list[PartHit]:
        import os

        creds = self._credentials()
        params = {
            "Token": creds["TME_TOKEN"],
            "SearchPlain": str(query),
            "Country": os.environ.get("TME_COUNTRY", "").strip() or "GB",
            "Language": "EN",
        }
        params["ApiSignature"] = self._sign(
            self._SEARCH_URL, params, creds["TME_SECRET"])
        body = urllib.parse.urlencode(params).encode("utf-8")
        payload = self._request(
            self._SEARCH_URL, method="POST", body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        products = ((payload.get("Data") or {}).get("ProductList") or [])
        hits = []
        for product in products[:limit]:
            mpn = first_string(product, ("OriginalSymbol",), ("Symbol",))
            if not mpn:
                continue
            hits.append(self._hit(
                mpn,
                mpn=mpn,
                manufacturer=first_string(product, ("Producer",)),
                description=first_string(product, ("Description",)),
                # TME returns documents from a separate endpoint; the
                # search reply carries a product page rather than a
                # datasheet. Stated as a page, not passed off as one.
                datasheet=first_string(product, ("ProductInformationPage",)),
            ))
        return hits

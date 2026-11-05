# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 StarX-ai
"""lib_generate_ipc_batch: what it sends, and what it refuses to call success."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from eda_agent.tools import ipc_batch


class FakeBridge:
    def __init__(self, output, *, create=True, step=True, error=False, text=False):
        self.output, self.create, self.step, self.error = output, create, step, error
        self.text, self.calls = text, []
        self.dispatch_params = ''

    async def send_command_async(self, command, args, **kwargs):
        self.calls.append(command)
        if command == 'generic.run_process':
            self.dispatch_params = args['params']
            if self.create:
                (self.output / 'test.PcbLib').write_bytes(b'fake')
                (self.output / 'report.html').write_text('<tr><td>input.csv</td><td>0</td><td>%s</td><td>1</td></tr>' % int(self.error))
            return {'dispatched': True}
        if command == 'library.get_footprints':
            return {'footprints': [{'name': 'QFN20'}]}
        if command == 'library.get_footprint_primitives':
            return {'body_models': [{'has_model': True, 'model_type': 1}] if self.step else [],
                    'texts': ([{'text': '.Designator', 'layer': 'Mechanical13'},
                               {'text': 'KEEP', 'layer': 'Mechanical13'}] if self.text else [])}
        if command == 'library.get_pad_geometry':
            return {'pad_count': 21}
        if command == 'application.save_document':
            return {'saved': True}
        raise AssertionError(f'Unexpected mutation {command}')


class BatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'input.csv'
        self.source.write_text('QFN,,\nFootprintName,DensityLevel,Other\nQFN20,N,\n')
        self.output = self.root / 'result'
        # As on a machine without Altium's templates; the template checks
        # have their own tests below.
        none = patch('eda_agent.design.ipc_templates.template_folders', return_value=[])
        none.start()
        self.addCleanup(none.stop)

    async def run_fake(self, fake, **kwargs):
        with patch.object(ipc_batch, 'get_bridge', return_value=fake), patch.object(
                ipc_batch.pcblib_models, 'extract_embedded_models', return_value={
                    'ok': True, 'written': [{'step_header': True, 'checksum_verified': True}]}):
            return await ipc_batch.generate_ipc_batch(str(self.source), str(self.output), **kwargs)

    async def test_checks_outputs_and_real_step(self):
        fake = FakeBridge(self.output)
        result = await self.run_fake(fake)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['footprint_count'], 1)
        self.assertFalse(result['ui_automation'])
        self.assertIn('|EnableStepModel=True', fake.dispatch_params)
        # Only Altium's own output is left in the folder.
        self.assertEqual(sorted(p.name for p in self.output.iterdir()), ['report.html', 'test.PcbLib'])

    async def test_dispatch_alone_is_not_success(self):
        fake = FakeBridge(self.output, create=False)
        result = await self.run_fake(fake)
        self.assertFalse(result['ok'])
        self.assertEqual(result['stage'], 'output')

    async def test_native_error_refuses_readback_and_save(self):
        fake = FakeBridge(self.output, error=True)
        result = await self.run_fake(fake)
        self.assertEqual(result['stage'], 'native_report')
        self.assertNotIn('application.save_document', fake.calls)

    async def test_extruded_or_missing_model_is_not_step(self):
        result = await self.run_fake(FakeBridge(self.output, step=False))
        self.assertEqual(result['stage'], '3d')
        self.assertFalse(result['ok'])

    async def test_preserves_unrelated_text(self):
        fake = FakeBridge(self.output, text=True)
        result = await self.run_fake(fake, remove_designator=True)
        self.assertEqual(result['stage'], 'designator')
        self.assertNotIn('library.delete_footprint_primitives', fake.calls)

    async def test_designator_text_is_kept_unless_asked(self):
        fake = FakeBridge(self.output, text=True)
        result = await self.run_fake(fake)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('library.delete_footprint_primitives', fake.calls)

    async def test_existing_directory_never_overwritten(self):
        self.output.mkdir()
        target = self.output / 'keep.PcbLib'
        target.write_bytes(b'keep')
        result = await self.run_fake(FakeBridge(self.output))
        self.assertEqual(result['stage'], 'preflight')
        self.assertEqual(target.read_bytes(), b'keep')

    async def test_rejects_pipe_injection(self):
        result = await ipc_batch.generate_ipc_batch(str(self.source)+'|FileName=x', str(self.output))
        self.assertEqual(result['stage'], 'preflight')

    async def test_csv_bad_density(self):
        # DensityLevel may be left blank (Altium's default); a wrong letter is refused.
        self.source.write_text('QFN,,\nFootprintName,DensityLevel,Other\nQFN20,X,\n')
        result = await self.run_fake(FakeBridge(self.output))
        self.assertEqual(result['stage'], 'preflight')
        self.assertIn('DensityLevel', result['reason'])


class ModelExportTests(unittest.TestCase):
    def test_duplicate_names_keep_distinct_records(self):
        import io, zlib
        from eda_agent.fileio import pcblib_models as models
        blobs = [b'ISO-10303-21;FIRST', b'ISO-10303-21;SECOND']
        class Ole:
            def openstream(self, name):
                return io.BytesIO(zlib.compress(blobs[int(name.rsplit('/', 1)[1])]))
            def close(self): pass
        def summary(i, rec, ole):
            return {'name': 'same.step', 'has_data': True,
                    'checksum': models.model_checksum(blobs[i]), 'index': i}
        with tempfile.TemporaryDirectory() as temp, patch.object(models, '_open', return_value=Ole()), patch.object(models, '_records', return_value=[{}, {}]), patch.object(models, '_summary', side_effect=summary):
            result = models.extract_embedded_models('fake.PcbLib', temp, per_record_directory=True)
            self.assertTrue(result['ok'], result)
            self.assertEqual([Path(m['path']).read_bytes() for m in result['written']], blobs)
            self.assertNotEqual(result['written'][0]['path'], result['written'][1]['path'])


if __name__ == '__main__':
    unittest.main()


# A template as ipc_templates reads one, cut down.
_QFN = {"family": "QFN", "file": "QFN.xls",
        "columns": ["FootprintName", "Dmin", "Dmax", "PitchD", "Pin1", "DensityLevel", "X"],
        "legend": [{"name": "FootprintName", "required": "Optional", "format": "String",
                    "description": "", "sheet": "Legend - Package"},
                   {"name": "Dmin, Dmax", "required": "Yes", "format": "Real",
                    "description": "body", "sheet": "Legend - Package"},
                   {"name": "PitchD", "required": "Yes", "format": "Real",
                    "description": "pitch", "sheet": "Legend - Package"},
                   {"name": "X", "required": "Yes", "format": "Real",
                    "description": "pad width", "sheet": "Legend - Footprint"}]}


def test_required_columns_are_the_package_ones():
    from eda_agent.design.ipc_templates import required_columns
    # "Dmin, Dmax" names two; the footprint legend's X is the generator's to work out.
    assert required_columns(_QFN) == ["Dmin", "Dmax", "PitchD"]


def test_a_table_is_checked_against_its_family():
    from eda_agent.design.ipc_templates import table_problems
    good = [["QFN"], ["FootprintName", "Dmin", "Dmax", "PitchD", "DensityLevel"],
            ["Q1", "2.9", "3.1", "0.5", "N"]]
    assert table_problems(good, _QFN, {"QFN", "SOT23", "DIP"}) == ([], 1)
    problems, _ = table_problems(
        [["QFN"], ["FootprintName", "Dmin", "PitchD", "Dmaks", "DensityLevel"],
         ["Q1", "2.9", "", "3", "X"]], _QFN, {"QFN"})
    text = " | ".join(problems)
    for bit in ("no columns ['Dmaks']", "required column Dmax is missing",
                "PitchD is empty on row(s) [3]", "DensityLevel is L, N or M"):
        assert bit in text, (bit, text)


def test_any_installed_family_is_accepted_and_others_named():
    from eda_agent.design.ipc_templates import table_problems
    rows = [["SOT23"], ["FootprintName"], ["S1"]]
    assert table_problems(rows, None, {"QFN", "SOT23"}) == ([], 1)
    problems, _ = table_problems([["SOT-23"], ["FootprintName"], ["S1"]], None,
                                 {"QFN", "SOT23"})
    assert "families are QFN, SOT23" in problems[0]
    # With no templates installed, Altium alone judges the family.
    assert table_problems(rows, None, set()) == ([], 1)


def test_the_installed_templates_read(tmp_path):
    from eda_agent.design import ipc_templates
    folders = ipc_templates.template_folders()
    if not folders:
        import pytest
        pytest.skip("Altium's IPC Footprint Generator templates are not installed here")
    rows = ipc_templates.families(folders[0])
    assert len(rows) > 10 and not any(r.get("error") for r in rows)
    qfn = ipc_templates.find_template(folders[0], "qfn")
    assert qfn["family"] == "QFN" and "PitchD" in qfn["columns"]
    assert {"Dmin", "Dmax", "PitchD", "Pin1"} <= set(ipc_templates.required_columns(qfn))
    # The code in A1 can differ from the file name.
    assert ipc_templates.find_template(folders[0], "QFP")["file"].endswith(
        "PQFP (Plastic Quad Flat Pack).xls")

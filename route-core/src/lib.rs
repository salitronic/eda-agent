// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
//! eda-agent's grid path search. Python builds the region and its costs
//! and gets the cheapest path back.

pub mod search;

#[cfg(feature = "python")]
mod py {
    use crate::search::{shortest_path as search, Region};
    use pyo3::exceptions::PyValueError;
    use pyo3::prelude::*;

    fn cast<T: Copy, const N: usize>(b: &[u8], what: &str, from: fn([u8; N]) -> T) -> PyResult<Vec<T>> {
        if b.len() % N != 0 {
            return Err(PyValueError::new_err(format!("{what}: {} bytes is not whole values", b.len())));
        }
        Ok(b.chunks_exact(N).map(|c| from(c.try_into().unwrap())).collect())
    }

    /// The cheapest path through a region of the routing grid.
    ///
    /// Arrays are little-endian bytes: cy, cx int32 (cells); cell_cost
    /// float64 (layers, cells); via_cost float64 (cells); move_factor
    /// float64 (layers, 8); potential float64 (cells) or None; sources and
    /// targets int64 node ids. Returns the node ids from a source to a
    /// target, or None.
    #[pyfunction]
    #[pyo3(signature = (w, h, layers, cy, cx, cell_cost, via_cost, move_factor, pitch, sources, targets, limit, potential=None))]
    #[allow(clippy::too_many_arguments)]
    fn shortest_path(
        py: Python<'_>,
        w: usize,
        h: usize,
        layers: usize,
        cy: &[u8],
        cx: &[u8],
        cell_cost: &[u8],
        via_cost: &[u8],
        move_factor: &[u8],
        pitch: f64,
        sources: &[u8],
        targets: &[u8],
        limit: f64,
        potential: Option<&[u8]>,
    ) -> PyResult<Option<Vec<i64>>> {
        let cy = cast(cy, "cy", i32::from_le_bytes)?;
        let cx = cast(cx, "cx", i32::from_le_bytes)?;
        let cc = cast(cell_cost, "cell_cost", f64::from_le_bytes)?;
        let vc = cast(via_cost, "via_cost", f64::from_le_bytes)?;
        let mf = cast(move_factor, "move_factor", f64::from_le_bytes)?;
        let src = cast(sources, "sources", i64::from_le_bytes)?;
        let tgt = cast(targets, "targets", i64::from_le_bytes)?;
        let pot = match potential {
            Some(p) => Some(cast(p, "potential", f64::from_le_bytes)?),
            None => None,
        };
        let r = Region { w, h, layers, cy: &cy, cx: &cx, cell_cost: &cc, via_cost: &vc,
                         move_factor: &mf, pitch, potential: pot.as_deref() };
        r.check().map_err(PyValueError::new_err)?;
        Ok(py.allow_threads(|| search(&r, &src, &tgt, limit)))
    }

    #[pymodule]
    fn eda_agent_route(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        m.add("API", 1)?;
        m.add_function(wrap_pyfunction!(shortest_path, m)?)?;
        Ok(())
    }
}

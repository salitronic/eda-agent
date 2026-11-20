// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
//! eda-agent's grid path search. Python builds the region and its costs
//! and gets the cheapest path back.

pub mod search;

#[cfg(feature = "python")]
mod py {
    use crate::search::{shortest_path as search, Region, Turns};
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
    ///
    /// `turn`: five step costs in cells, by how far a step turns from the
    /// one before (0, 45, 90, 135, 180 degrees; infinite bars it), with
    /// `no_squeeze` barring a diagonal step beside a barred cell; None
    /// searches without knowing which way a step went. With turns,
    /// `source_heading` (one byte per source: the move it is already
    /// heading along, 8 for none) and `target_moves` (one byte per target:
    /// a bit per move it may be reached by, 255 for any).
    #[pyfunction]
    #[pyo3(signature = (w, h, layers, cy, cx, cell_cost, via_cost, move_factor, pitch, sources, targets, limit, potential=None, turn=None, no_squeeze=false, source_heading=None, target_moves=None))]
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
        turn: Option<Vec<f64>>,
        no_squeeze: bool,
        source_heading: Option<&[u8]>,
        target_moves: Option<&[u8]>,
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
        if let Some(sh) = source_heading {
            if sh.len() != src.len() || sh.iter().any(|&b| b > 8) {
                return Err(PyValueError::new_err(format!(
                    "source_heading: {} headings (0 to 8) for {} sources", sh.len(), src.len())));
            }
        }
        if let Some(tm) = target_moves {
            if tm.len() != tgt.len() {
                return Err(PyValueError::new_err(format!(
                    "target_moves: {} masks for {} targets", tm.len(), tgt.len())));
            }
        }
        let turns = match turn {
            Some(t) => {
                let t: [f64; 5] = t.try_into().map_err(|t: Vec<f64>| {
                    PyValueError::new_err(format!("turn: {} costs, want 5", t.len()))
                })?;
                Some(Turns { turn: t, no_squeeze, source_heading, target_moves })
            }
            None => None,
        };
        let r = Region { w, h, layers, cy: &cy, cx: &cx, cell_cost: &cc, via_cost: &vc,
                         move_factor: &mf, pitch, potential: pot.as_deref(), turns };
        r.check().map_err(PyValueError::new_err)?;
        Ok(py.allow_threads(|| search(&r, &src, &tgt, limit)))
    }

    #[pymodule]
    fn eda_agent_route(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        m.add("API", 2)?;
        m.add_function(wrap_pyfunction!(shortest_path, m)?)?;
        Ok(())
    }
}

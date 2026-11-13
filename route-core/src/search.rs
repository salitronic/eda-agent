// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
//! The cheapest path through a region of the routing grid.
//!
//! The region and its arcs are the ones eda-agent's search.py builds as a
//! sparse matrix for scipy: one node per region cell on every layer, then
//! one via node per cell. A layer node joins its eight neighbours in the
//! region; a via node joins its cell on every layer, each way costing half
//! the via. Weights are computed with the same operations in the same
//! order, so a path's cost is the same to the last bit. An arc whose
//! weight reaches BLOCKED is not an arc.
//!
//! Unlike scipy, the search stops at the first target it settles, and
//! builds no graph: each node's arcs are worked out when it is settled.
//! With the potential (a lower bound on the cost to go, already folded
//! into the weights) that is A*.

use std::cmp::Reverse;
use std::collections::BinaryHeap;

pub const BLOCKED: f64 = 1e12;

/// The eight in-plane moves, (dx, dy, length in cells), in search.py's order.
const MOVES: [(i32, i32, bool); 8] = [
    (1, 0, false),
    (-1, 0, false),
    (0, 1, false),
    (0, -1, false),
    (1, 1, true),
    (1, -1, true),
    (-1, 1, true),
    (-1, -1, true),
];

/// One region of the grid and its costs. Arrays are row-major as numpy
/// gives them: `cell_cost` (layers, cells), `move_factor` (layers, 8).
pub struct Region<'a> {
    pub w: usize,
    pub h: usize,
    pub layers: usize,
    pub cy: &'a [i32],
    pub cx: &'a [i32],
    pub cell_cost: &'a [f64],
    pub via_cost: &'a [f64],
    pub move_factor: &'a [f64],
    pub pitch: f64,
    pub potential: Option<&'a [f64]>,
}

impl<'a> Region<'a> {
    /// Err names the array that does not fit the region.
    pub fn check(&self) -> Result<(), String> {
        let m = self.cy.len();
        if self.cx.len() != m {
            return Err(format!("{} x for {} y", self.cx.len(), m));
        }
        if self.cell_cost.len() != self.layers * m {
            return Err(format!("{} cell costs for {} layers of {} cells", self.cell_cost.len(), self.layers, m));
        }
        if self.via_cost.len() != m {
            return Err(format!("{} via costs for {} cells", self.via_cost.len(), m));
        }
        if self.move_factor.len() != self.layers * 8 {
            return Err(format!("{} move factors for {} layers", self.move_factor.len(), self.layers));
        }
        if let Some(p) = self.potential {
            if p.len() != m {
                return Err(format!("{} potentials for {} cells", p.len(), m));
            }
        }
        for i in 0..m {
            let (y, x) = (self.cy[i], self.cx[i]);
            if y < 0 || x < 0 || y as usize >= self.h || x as usize >= self.w {
                return Err(format!("cell {i} at ({y}, {x}) is outside the {}x{} box", self.h, self.w));
            }
        }
        Ok(())
    }

    fn cells(&self) -> usize {
        self.cy.len()
    }
}

/// The cheapest path from any source node to any target node, as node ids
/// from source to target, or None when none costs `limit` or less (and
/// under BLOCKED).
pub fn shortest_path(r: &Region, sources: &[i64], targets: &[i64], limit: f64) -> Option<Vec<i64>> {
    let m = r.cells();
    let nl = r.layers * m;
    let size = nl + m;
    if sources.is_empty() || targets.is_empty() || m == 0 {
        return None;
    }
    let limit = limit.min(BLOCKED / 2.0);
    // The region's ranks, with a border of -1 that a move off the box reads.
    let (bw, bh) = (r.w + 2, r.h + 2);
    let mut rank = vec![-1i32; bw * bh];
    for i in 0..m {
        rank[(r.cy[i] as usize + 1) * bw + r.cx[i] as usize + 1] = i as i32;
    }
    let cc = |l: usize, i: usize| r.cell_cost[l * m + i].min(BLOCKED);
    let vc = |i: usize| r.via_cost[i].min(BLOCKED);
    let blocked = |l: usize, i: usize| if cc(l, i) >= BLOCKED { BLOCKED } else { 0.0 };
    let diag = std::f64::consts::SQRT_2;

    let mut is_target = vec![false; size];
    for &t in targets {
        if t >= 0 && (t as usize) < size {
            is_target[t as usize] = true;
        }
    }
    let mut dist = vec![f64::INFINITY; size];
    let mut pred = vec![-1i64; size];
    let mut done = vec![false; size];
    let mut heap: BinaryHeap<(Reverse<u64>, Reverse<usize>)> = BinaryHeap::new();
    for &s in sources {
        if s >= 0 && (s as usize) < size && dist[s as usize] != 0.0 {
            dist[s as usize] = 0.0;
            heap.push((Reverse(0f64.to_bits()), Reverse(s as usize)));
        }
    }
    // Distances are never negative, so their bits order as they do.
    let relax = |heap: &mut BinaryHeap<_>, dist: &mut Vec<f64>, pred: &mut Vec<i64>, u: usize, v: usize, w: f64| {
        if w >= BLOCKED {
            return;
        }
        let nd = dist[u] + w;
        if nd <= limit && nd < dist[v] {
            dist[v] = nd;
            pred[v] = u as i64;
            heap.push((Reverse(nd.to_bits()), Reverse(v)));
        }
    };
    let mut found = None;
    while let Some((Reverse(bits), Reverse(u))) = heap.pop() {
        if done[u] || f64::from_bits(bits) > dist[u] {
            continue;
        }
        done[u] = true;
        if is_target[u] {
            found = Some(u);
            break;
        }
        if u < nl {
            let (l, i) = (u / m, u % m);
            let (y, x) = (r.cy[i] as usize + 1, r.cx[i] as usize + 1);
            for (k, &(dx, dy, is_diag)) in MOVES.iter().enumerate() {
                let nb = rank[((y as i32 + dy) as usize) * bw + (x as i32 + dx) as usize];
                if nb < 0 {
                    continue;
                }
                let nb = nb as usize;
                let length = if is_diag { diag } else { 1.0 };
                let mut wk = cc(l, nb) * ((length * r.pitch) * r.move_factor[l * 8 + k]);
                if let Some(p) = r.potential {
                    wk += p[nb] - p[i];
                    if wk < 0.0 {
                        wk = 0.0;
                    }
                }
                relax(&mut heap, &mut dist, &mut pred, u, l * m + nb, wk);
            }
            relax(&mut heap, &mut dist, &mut pred, u, nl + i, vc(i) / 2.0 + blocked(l, i));
        } else {
            let i = u - nl;
            for l in 0..r.layers {
                relax(&mut heap, &mut dist, &mut pred, u, l * m + i, vc(i) / 2.0 + blocked(l, i));
            }
        }
    }
    let t = found?;
    if dist[t] >= BLOCKED {
        return None;
    }
    let mut path = vec![t as i64];
    let mut n = t;
    while pred[n] >= 0 {
        n = pred[n] as usize;
        path.push(n as i64);
    }
    path.reverse();
    Some(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn line(cost: f64) -> (Vec<i32>, Vec<i32>, Vec<f64>) {
        (vec![0; 5], (0..5).collect(), vec![cost; 5])
    }

    #[test]
    fn a_straight_line_costs_its_length() {
        let (cy, cx, cost) = line(1.0);
        let r = Region { w: 5, h: 1, layers: 1, cy: &cy, cx: &cx, cell_cost: &cost,
                         via_cost: &[f64::INFINITY; 5], move_factor: &[1.0; 8], pitch: 2.0, potential: None };
        r.check().unwrap();
        assert_eq!(shortest_path(&r, &[0], &[4], 1e9), Some(vec![0, 1, 2, 3, 4]));
        assert_eq!(shortest_path(&r, &[0], &[4], 7.9), None);
    }

    #[test]
    fn a_wall_is_passed_through_the_other_layer() {
        let (cy, cx, _) = line(1.0);
        let mut cost = vec![1.0; 10];
        cost[2] = f64::INFINITY;
        let r = Region { w: 5, h: 1, layers: 2, cy: &cy, cx: &cx, cell_cost: &cost,
                         via_cost: &[4.0; 5], move_factor: &[1.0; 16], pitch: 1.0, potential: None };
        let p = shortest_path(&r, &[0], &[4], 1e9).unwrap();
        assert!(p.iter().any(|&n| n >= 10), "{p:?}");
        assert!(!p.contains(&2));
    }
}

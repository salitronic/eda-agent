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
    /// With turns, the search knows which way each step went. `turn` is
    /// what a step costs on top, in cells, by how far it turns from the
    /// step before: 0, 45, 90, 135 and 180 degrees; infinite bars that
    /// turn. A step out of a via, or out of a source given no heading,
    /// turns from nothing.
    pub turns: Option<Turns<'a>>,
}

/// The cost of turning, whether a diagonal step may pass between two
/// barred cells, and which way the route leaves and reaches its ends.
#[derive(Clone, Copy)]
pub struct Turns<'a> {
    pub turn: [f64; 5],
    /// A diagonal step is barred where either cell beside it, on its
    /// layer, is barred: a track may not squeeze through a corner.
    pub no_squeeze: bool,
    /// Per source, the move the route is already heading along there (a
    /// pad's stub, outward), or FRESH (8) for none.
    pub source_heading: Option<&'a [u8]>,
    /// Per target, the moves it may be reached by, one bit each (bit k:
    /// MOVES[k]); 255 for any. A target node listed twice takes either.
    pub target_moves: Option<&'a [u8]>,
}

impl<'a> Turns<'a> {
    pub fn new(turn: [f64; 5], no_squeeze: bool) -> Self {
        Turns { turn, no_squeeze, source_heading: None, target_moves: None }
    }
}

/// How far move `b` turns from move `a`: 0 (none) to 4 (straight back), in
/// steps of 45 degrees. search.py has the same table.
pub fn turn_class(a: usize, b: usize) -> usize {
    let (ax, ay, _) = MOVES[a];
    let (bx, by, _) = MOVES[b];
    let dot = (ax * bx + ay * by) as f64;
    let la = ((ax * ax + ay * ay) as f64).sqrt();
    let lb = ((bx * bx + by * by) as f64).sqrt();
    let c = dot / (la * lb);
    if c > 0.9 {
        0
    } else if c > 0.5 {
        1
    } else if c > -0.5 {
        2
    } else if c > -0.9 {
        3
    } else {
        4
    }
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
    if let Some(t) = r.turns {
        return shortest_path_turning(r, &t, sources, targets, limit);
    }
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

/// No step yet: out of a source or a via.
const FRESH: usize = 8;

/// shortest_path when the search knows which way each step went. A layer
/// node has nine states, one per move it was entered by and FRESH; a via
/// node has one. State ids: layer node n, heading h is n * 9 + h; via
/// node nl + i is 9 * nl + i. The path is given back as node ids.
fn shortest_path_turning(r: &Region, t: &Turns, sources: &[i64], targets: &[i64], limit: f64) -> Option<Vec<i64>> {
    let m = r.cells();
    let nl = r.layers * m;
    if sources.is_empty() || targets.is_empty() || m == 0 {
        return None;
    }
    let size = 9 * nl + m;
    let limit = limit.min(BLOCKED / 2.0);
    let (bw, bh) = (r.w + 2, r.h + 2);
    let mut rank = vec![-1i32; bw * bh];
    for i in 0..m {
        rank[(r.cy[i] as usize + 1) * bw + r.cx[i] as usize + 1] = i as i32;
    }
    let cc = |l: usize, i: usize| r.cell_cost[l * m + i].min(BLOCKED);
    let vc = |i: usize| r.via_cost[i].min(BLOCKED);
    let blocked = |l: usize, i: usize| if cc(l, i) >= BLOCKED { BLOCKED } else { 0.0 };
    let diag = std::f64::consts::SQRT_2;
    let mut turn_w = [[0.0f64; 8]; 9];
    for (h, row) in turn_w.iter_mut().enumerate() {
        for (k, w) in row.iter_mut().enumerate() {
            *w = if h == FRESH { 0.0 } else { t.turn[turn_class(h, k)] * r.pitch };
        }
    }
    let state_of = |n: i64, h: usize| -> Option<usize> {
        if n < 0 {
            return None;
        }
        let n = n as usize;
        if n < nl {
            Some(n * 9 + h.min(FRESH))
        } else if n < nl + m {
            Some(9 * nl + n - nl)
        } else {
            None
        }
    };
    let node_of = |s: usize| -> i64 { if s < 9 * nl { (s / 9) as i64 } else { (nl + s - 9 * nl) as i64 } };

    // Per node, the moves it may be reached by as a target (0: not one).
    let mut reach = vec![0u8; nl + m];
    for (j, &x) in targets.iter().enumerate() {
        if x >= 0 && (x as usize) < nl + m {
            let moves = t.target_moves.and_then(|tm| tm.get(j).copied()).unwrap_or(255);
            reach[x as usize] |= moves;
        }
    }
    // A layer state is a target when its node is and it was entered by a
    // move the node takes; FRESH (a source standing on a target) always.
    let is_target = |s: usize| -> bool {
        if s >= 9 * nl {
            return reach[nl + s - 9 * nl] != 0;
        }
        let (n, h) = (s / 9, s % 9);
        reach[n] != 0 && (h == FRESH || reach[n] & (1u8 << h) != 0)
    };
    let mut dist = vec![f64::INFINITY; size];
    let mut pred = vec![-1i64; size];
    let mut done = vec![false; size];
    let mut heap: BinaryHeap<(Reverse<u64>, Reverse<usize>)> = BinaryHeap::new();
    for (j, &s) in sources.iter().enumerate() {
        let h = t.source_heading.and_then(|sh| sh.get(j).copied()).unwrap_or(FRESH as u8);
        if let Some(s) = state_of(s, h as usize) {
            if dist[s] != 0.0 {
                dist[s] = 0.0;
                heap.push((Reverse(0f64.to_bits()), Reverse(s)));
            }
        }
    }
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
        if is_target(u) {
            found = Some(u);
            break;
        }
        if u < 9 * nl {
            let (n, h) = (u / 9, u % 9);
            let (l, i) = (n / m, n % m);
            let (y, x) = (r.cy[i] as usize + 1, r.cx[i] as usize + 1);
            for (k, &(dx, dy, is_diag)) in MOVES.iter().enumerate() {
                let tw = turn_w[h][k];
                if !(tw < BLOCKED) {
                    continue;
                }
                let nb = rank[((y as i32 + dy) as usize) * bw + (x as i32 + dx) as usize];
                if nb < 0 {
                    continue;
                }
                let nb = nb as usize;
                if is_diag && t.no_squeeze {
                    let a = rank[y * bw + (x as i32 + dx) as usize];
                    let b = rank[((y as i32 + dy) as usize) * bw + x];
                    if (a >= 0 && cc(l, a as usize) >= BLOCKED) || (b >= 0 && cc(l, b as usize) >= BLOCKED) {
                        continue;
                    }
                }
                let length = if is_diag { diag } else { 1.0 };
                let mut wk = cc(l, nb) * ((length * r.pitch) * r.move_factor[l * 8 + k]);
                wk += tw;
                if let Some(p) = r.potential {
                    wk += p[nb] - p[i];
                    if wk < 0.0 {
                        wk = 0.0;
                    }
                }
                relax(&mut heap, &mut dist, &mut pred, u, (l * m + nb) * 9 + k, wk);
            }
            relax(&mut heap, &mut dist, &mut pred, u, 9 * nl + i, vc(i) / 2.0 + blocked(l, i));
        } else {
            let i = u - 9 * nl;
            for l in 0..r.layers {
                relax(&mut heap, &mut dist, &mut pred, u, (l * m + i) * 9 + FRESH, vc(i) / 2.0 + blocked(l, i));
            }
        }
    }
    let t_state = found?;
    if dist[t_state] >= BLOCKED {
        return None;
    }
    let mut path = vec![node_of(t_state)];
    let mut s = t_state;
    while pred[s] >= 0 {
        s = pred[s] as usize;
        path.push(node_of(s));
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
                         via_cost: &[f64::INFINITY; 5], move_factor: &[1.0; 8], pitch: 2.0, potential: None, turns: None };
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
                         via_cost: &[4.0; 5], move_factor: &[1.0; 16], pitch: 1.0, potential: None, turns: None };
        let p = shortest_path(&r, &[0], &[4], 1e9).unwrap();
        assert!(p.iter().any(|&n| n >= 10), "{p:?}");
        assert!(!p.contains(&2));
    }

    fn open_box(w: usize, h: usize) -> (Vec<i32>, Vec<i32>) {
        let mut cy = vec![];
        let mut cx = vec![];
        for y in 0..h {
            for x in 0..w {
                cy.push(y as i32);
                cx.push(x as i32);
            }
        }
        (cy, cx)
    }

    fn bends(p: &[i64], w: i64) -> usize {
        let d: Vec<(i64, i64)> = p.windows(2).map(|s| (s[1] % w - s[0] % w, s[1] / w - s[0] / w)).collect();
        d.windows(2).filter(|s| s[0] != s[1]).count()
    }

    #[test]
    fn with_turns_a_shallow_run_bends_once() {
        // 12 across and 3 down: as cheap as a staircase without turns, one
        // 45 degree bend with them.
        let (cy, cx) = open_box(13, 4);
        let cost = vec![1.0; cy.len()];
        let r = Region { w: 13, h: 4, layers: 1, cy: &cy, cx: &cx, cell_cost: &cost,
                         via_cost: &vec![f64::INFINITY; cy.len()], move_factor: &[1.0; 8], pitch: 1.0,
                         potential: None,
                         turns: Some(Turns::new([0.0, 0.6, 3.0, f64::INFINITY, f64::INFINITY], true)) };
        let p = shortest_path(&r, &[0], &[3 * 13 + 12], 1e9).unwrap();
        assert_eq!(bends(&p, 13), 1, "{p:?}");
    }

    #[test]
    fn with_turns_no_diagonal_squeezes_between_two_walls() {
        // Cells (1, 0) and (0, 1) are barred: (0, 0) to (1, 1) may not cut
        // between them.
        let (cy, cx) = open_box(3, 3);
        let mut cost = vec![1.0; 9];
        cost[1] = f64::INFINITY;
        cost[3] = f64::INFINITY;
        let turns = Some(Turns::new([0.0, 0.6, 3.0, f64::INFINITY, f64::INFINITY], true));
        let r = Region { w: 3, h: 3, layers: 1, cy: &cy, cx: &cx, cell_cost: &cost,
                         via_cost: &[f64::INFINITY; 9], move_factor: &[1.0; 8], pitch: 1.0,
                         potential: None, turns };
        assert_eq!(shortest_path(&r, &[0], &[4], 1e9), None);
    }

    #[test]
    fn a_source_heading_and_target_moves_keep_the_ends_on_their_stubs() {
        // From (0, 3) heading east, to (8, 0) reached only heading north
        // (move 3, dy -1) or 45 degrees off it: no 90 degree turn off the
        // heading at the start, and the last step is one the target takes.
        let (cy, cx) = open_box(9, 7);
        let cost = vec![1.0; cy.len()];
        let heading = [0u8];
        let moves = [(1u8 << 3) | (1 << 5) | (1 << 7)];
        let mut turns = Turns::new([0.0, 0.6, 3.0, f64::INFINITY, f64::INFINITY], true);
        turns.source_heading = Some(&heading);
        turns.target_moves = Some(&moves);
        let r = Region { w: 9, h: 7, layers: 1, cy: &cy, cx: &cx, cell_cost: &cost,
                         via_cost: &vec![f64::INFINITY; cy.len()], move_factor: &[1.0; 8], pitch: 1.0,
                         potential: None, turns: Some(turns) };
        let p = shortest_path(&r, &[3 * 9], &[8], 1e9).unwrap();
        let step = |a: i64, b: i64| (b % 9 - a % 9, b / 9 - a / 9);
        let first = step(p[0], p[1]);
        let last = step(p[p.len() - 2], p[p.len() - 1]);
        assert!(first.0 > 0, "{p:?}");
        assert!(last.1 < 0, "{p:?}");
    }
}

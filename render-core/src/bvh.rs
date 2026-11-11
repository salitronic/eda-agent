// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
//! A bounding volume hierarchy over triangles, built with binned SAH.

use crate::math::V3;

#[derive(Clone, Copy)]
pub struct Aabb {
    pub lo: V3,
    pub hi: V3,
}

impl Aabb {
    pub fn empty() -> Self {
        Aabb { lo: V3::splat(f32::INFINITY), hi: V3::splat(f32::NEG_INFINITY) }
    }
    pub fn grow(&mut self, p: V3) {
        self.lo = self.lo.min(p);
        self.hi = self.hi.max(p);
    }
    pub fn merge(&mut self, b: &Aabb) {
        self.lo = self.lo.min(b.lo);
        self.hi = self.hi.max(b.hi);
    }
    pub fn area(&self) -> f32 {
        let d = self.hi - self.lo;
        if d.x < 0.0 {
            return 0.0;
        }
        2.0 * (d.x * d.y + d.y * d.z + d.z * d.x)
    }
    /// Entry distance of a ray, or infinity.
    #[inline]
    pub fn hit(&self, o: V3, inv: V3, tmax: f32) -> f32 {
        let t1 = (self.lo - o).mul(inv);
        let t2 = (self.hi - o).mul(inv);
        let tn = t1.min(t2).max_comp().max(0.0);
        let tf = t1.max(t2);
        let tf = tf.x.min(tf.y).min(tf.z).min(tmax);
        if tn <= tf {
            tn
        } else {
            f32::INFINITY
        }
    }
}

/// A node: either two children (left at `first`, right at `first + 1`)
/// or a leaf of `count` triangles from `first` in the index list.
#[derive(Clone, Copy)]
pub struct Node {
    pub bounds: Aabb,
    pub first: u32,
    pub count: u32,
}

pub struct Bvh {
    pub nodes: Vec<Node>,
    pub order: Vec<u32>,
}

const BINS: usize = 16;
const LEAF: usize = 4;

pub fn build(boxes: &[Aabb], centres: &[V3]) -> Bvh {
    let n = boxes.len();
    let mut order: Vec<u32> = (0..n as u32).collect();
    let mut nodes = Vec::with_capacity(2 * n.max(1));
    nodes.push(Node { bounds: Aabb::empty(), first: 0, count: n as u32 });
    if n == 0 {
        return Bvh { nodes, order };
    }
    let mut stack = vec![0usize];
    while let Some(ni) = stack.pop() {
        let (first, count) = (nodes[ni].first as usize, nodes[ni].count as usize);
        let mut b = Aabb::empty();
        let mut cb = Aabb::empty();
        for &t in &order[first..first + count] {
            b.merge(&boxes[t as usize]);
            cb.grow(centres[t as usize]);
        }
        nodes[ni].bounds = b;
        if count <= LEAF {
            continue;
        }
        // Binned SAH over the widest centroid axis, then the others.
        let mut best = (f32::INFINITY, 0usize, 0usize);
        for axis in 0..3 {
            let lo = cb.lo.axis(axis);
            let ext = cb.hi.axis(axis) - lo;
            if ext <= 1e-12 {
                continue;
            }
            let mut bins = [(Aabb::empty(), 0usize); BINS];
            for &t in &order[first..first + count] {
                let k = (((centres[t as usize].axis(axis) - lo) / ext) * BINS as f32) as usize;
                let k = k.min(BINS - 1);
                bins[k].0.merge(&boxes[t as usize]);
                bins[k].1 += 1;
            }
            let mut left = [(0.0f32, 0usize); BINS];
            let mut acc = Aabb::empty();
            let mut cnt = 0;
            for i in 0..BINS {
                acc.merge(&bins[i].0);
                cnt += bins[i].1;
                left[i] = (acc.area(), cnt);
            }
            let mut acc = Aabb::empty();
            let mut cnt = 0;
            for i in (1..BINS).rev() {
                acc.merge(&bins[i].0);
                cnt += bins[i].1;
                let (la, lc) = left[i - 1];
                if lc == 0 || cnt == 0 {
                    continue;
                }
                let cost = la * lc as f32 + acc.area() * cnt as f32;
                if cost < best.0 {
                    best = (cost, axis, i);
                }
            }
        }
        let leaf_cost = b.area() * count as f32;
        if best.0 >= leaf_cost && count <= 16 {
            continue;
        }
        let mid = if best.0.is_finite() {
            let (_, axis, split) = best;
            let lo = cb.lo.axis(axis);
            let ext = cb.hi.axis(axis) - lo;
            let slice = &mut order[first..first + count];
            let mut i = 0;
            let mut j = slice.len();
            while i < j {
                let c = centres[slice[i] as usize].axis(axis);
                let k = ((((c - lo) / ext) * BINS as f32) as usize).min(BINS - 1);
                if k < split {
                    i += 1;
                } else {
                    j -= 1;
                    slice.swap(i, j);
                }
            }
            i
        } else {
            count / 2
        };
        if mid == 0 || mid == count {
            continue;
        }
        let l = nodes.len();
        nodes.push(Node { bounds: Aabb::empty(), first: first as u32, count: mid as u32 });
        nodes.push(Node {
            bounds: Aabb::empty(),
            first: (first + mid) as u32,
            count: (count - mid) as u32,
        });
        nodes[ni].first = l as u32;
        nodes[ni].count = 0;
        stack.push(l);
        stack.push(l + 1);
    }
    Bvh { nodes, order }
}

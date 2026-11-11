// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
//! Triangles, materials and textures, and finding what a ray hits.

use crate::bvh::{self, Aabb, Bvh};
use crate::math::V3;

/// A surface. With a texture, the texture's colour replaces `base`, its
/// alpha cuts holes (below one half the surface is not there) and its
/// parameter map, when given, replaces roughness and metallic per texel.
#[derive(Clone, Copy, Debug)]
pub struct Material {
    pub base: V3,
    pub roughness: f32,
    pub metallic: f32,
    pub emission: V3,
    pub texture: i32,
    /// 0 opaque .. 1 clear: a clear coat over the base (solder mask).
    pub coat: f32,
    /// Height of the texture's relief at full scale (parameter map
    /// alpha 255), scene units; 0 is flat.
    pub bump: f32,
    /// 0 opaque .. 1 clear: light passes into the surface and refracts,
    /// as through glass or clear resin (index 1.5), tinted by `base`.
    pub transmission: f32,
}

pub struct Texture {
    pub w: usize,
    pub h: usize,
    /// RGBA8, rows bottom to top.
    pub rgba: Vec<u8>,
    /// Optional RGBA8: R roughness, G metallic, B coat, A height.
    pub params: Option<Vec<u8>>,
}

#[derive(Clone, Copy, Debug)]
pub struct Surface {
    pub base: V3,
    pub roughness: f32,
    pub metallic: f32,
    pub emission: V3,
    pub coat: f32,
    pub alpha: f32,
    pub transmission: f32,
}

fn srgb_to_linear(c: u8) -> f32 {
    let c = c as f32 / 255.0;
    if c <= 0.04045 {
        c / 12.92
    } else {
        ((c + 0.055) / 1.055).powf(2.4)
    }
}

impl Texture {
    fn texel(&self, x: usize, y: usize) -> [u8; 4] {
        let i = 4 * (y * self.w + x);
        [self.rgba[i], self.rgba[i + 1], self.rgba[i + 2], self.rgba[i + 3]]
    }
    /// Colour (linear) and alpha at uv, bilinear.
    pub fn sample(&self, u: f32, v: f32) -> (V3, f32) {
        let fx = (u.clamp(0.0, 1.0) * self.w as f32 - 0.5).max(0.0);
        let fy = (v.clamp(0.0, 1.0) * self.h as f32 - 0.5).max(0.0);
        let x0 = (fx as usize).min(self.w - 1);
        let y0 = (fy as usize).min(self.h - 1);
        let x1 = (x0 + 1).min(self.w - 1);
        let y1 = (y0 + 1).min(self.h - 1);
        let tx = fx - x0 as f32;
        let ty = fy - y0 as f32;
        let mut col = V3::default();
        let mut a = 0.0;
        for (x, y, w) in [
            (x0, y0, (1.0 - tx) * (1.0 - ty)),
            (x1, y0, tx * (1.0 - ty)),
            (x0, y1, (1.0 - tx) * ty),
            (x1, y1, tx * ty),
        ] {
            let t = self.texel(x, y);
            col += V3::new(srgb_to_linear(t[0]), srgb_to_linear(t[1]), srgb_to_linear(t[2])) * w;
            a += t[3] as f32 / 255.0 * w;
        }
        (col, a)
    }
    /// Roughness, metallic and coat at uv, nearest texel.
    pub fn params_at(&self, u: f32, v: f32) -> Option<(f32, f32, f32)> {
        let p = self.params.as_ref()?;
        let x = ((u.clamp(0.0, 1.0) * self.w as f32) as usize).min(self.w - 1);
        let y = ((v.clamp(0.0, 1.0) * self.h as f32) as usize).min(self.h - 1);
        let i = 4 * (y * self.w + x);
        Some((p[i] as f32 / 255.0, p[i + 1] as f32 / 255.0, p[i + 2] as f32 / 255.0))
    }
    /// Relief height at uv, 0..1, bilinear.
    pub fn height_at(&self, u: f32, v: f32) -> f32 {
        let p = match self.params.as_ref() {
            Some(p) => p,
            None => return 0.0,
        };
        let fx = (u.clamp(0.0, 1.0) * self.w as f32 - 0.5).max(0.0);
        let fy = (v.clamp(0.0, 1.0) * self.h as f32 - 0.5).max(0.0);
        let x0 = (fx as usize).min(self.w - 1);
        let y0 = (fy as usize).min(self.h - 1);
        let x1 = (x0 + 1).min(self.w - 1);
        let y1 = (y0 + 1).min(self.h - 1);
        let tx = fx - x0 as f32;
        let ty = fy - y0 as f32;
        let at = |x: usize, y: usize| p[4 * (y * self.w + x) + 3] as f32 / 255.0;
        (at(x0, y0) * (1.0 - tx) + at(x1, y0) * tx) * (1.0 - ty)
            + (at(x0, y1) * (1.0 - tx) + at(x1, y1) * tx) * ty
    }
}

/// A triangle kept ready for intersection: v0 and two edges.
#[derive(Clone, Copy)]
struct Tri {
    v0: V3,
    e1: V3,
    e2: V3,
}

pub struct Hit {
    pub t: f32,
    pub tri: u32,
    pub u: f32,
    pub v: f32,
}

pub struct Scene {
    pub pos: Vec<V3>,
    pub nrm: Option<Vec<V3>>,
    pub uv: Option<Vec<[f32; 2]>>,
    pub idx: Vec<[u32; 3]>,
    pub mat: Vec<u32>,
    pub materials: Vec<Material>,
    pub textures: Vec<Texture>,
    tris: Vec<Tri>,
    bvh: Bvh,
    /// Whether a triangle's material can cut holes (has a texture).
    cutout: Vec<bool>,
    /// Whether light passes a triangle: shadow rays go through it.
    clear: Vec<bool>,
}

impl Scene {
    pub fn new(
        pos: Vec<V3>,
        nrm: Option<Vec<V3>>,
        uv: Option<Vec<[f32; 2]>>,
        idx: Vec<[u32; 3]>,
        mat: Vec<u32>,
        materials: Vec<Material>,
        textures: Vec<Texture>,
    ) -> Self {
        let mut boxes = Vec::with_capacity(idx.len());
        let mut centres = Vec::with_capacity(idx.len());
        let mut tris = Vec::with_capacity(idx.len());
        for t in &idx {
            let (a, b, c) = (pos[t[0] as usize], pos[t[1] as usize], pos[t[2] as usize]);
            let mut bb = Aabb::empty();
            bb.grow(a);
            bb.grow(b);
            bb.grow(c);
            boxes.push(bb);
            centres.push((a + b + c) / 3.0);
            tris.push(Tri { v0: a, e1: b - a, e2: c - a });
        }
        let bvh = bvh::build(&boxes, &centres);
        let order = &bvh.order;
        let tris: Vec<Tri> = order.iter().map(|&i| tris[i as usize]).collect();
        let idx2: Vec<[u32; 3]> = order.iter().map(|&i| idx[i as usize]).collect();
        let mat2: Vec<u32> = order.iter().map(|&i| mat[i as usize]).collect();
        let cutout = mat2
            .iter()
            .map(|&m| materials.get(m as usize).map_or(false, |mm| mm.texture >= 0))
            .collect();
        let clear = mat2
            .iter()
            .map(|&m| materials.get(m as usize).map_or(false, |mm| mm.transmission > 0.5))
            .collect();
        Scene { pos, nrm, uv, idx: idx2, mat: mat2, materials, textures, tris, bvh, cutout, clear }
    }

    pub fn triangle_count(&self) -> usize {
        self.idx.len()
    }

    /// Bounds of everything in the scene.
    pub fn bounds(&self) -> Aabb {
        self.bvh.nodes.first().map(|n| n.bounds).unwrap_or(Aabb::empty())
    }

    #[inline]
    fn tri_hit(&self, i: usize, o: V3, d: V3, tmax: f32) -> Option<(f32, f32, f32)> {
        let tr = &self.tris[i];
        let p = d.cross(tr.e2);
        let det = tr.e1.dot(p);
        if det.abs() < 1e-12 {
            return None;
        }
        let inv = 1.0 / det;
        let s = o - tr.v0;
        let u = s.dot(p) * inv;
        if !(0.0..=1.0).contains(&u) {
            return None;
        }
        let q = s.cross(tr.e1);
        let v = d.dot(q) * inv;
        if v < 0.0 || u + v > 1.0 {
            return None;
        }
        let t = tr.e2.dot(q) * inv;
        if t > 1e-6 && t < tmax {
            Some((t, u, v))
        } else {
            None
        }
    }

    /// Whether the surface really is there at this point of triangle i.
    fn solid(&self, i: usize, u: f32, v: f32) -> bool {
        if !self.cutout[i] {
            return true;
        }
        let m = &self.materials[self.mat[i] as usize];
        match (self.textures.get(m.texture as usize), &self.uv) {
            (Some(tex), Some(uvs)) => {
                let [a, b, c] = self.idx[i];
                let (ua, ub, uc) = (uvs[a as usize], uvs[b as usize], uvs[c as usize]);
                let w = 1.0 - u - v;
                let tu = ua[0] * w + ub[0] * u + uc[0] * v;
                let tv = ua[1] * w + ub[1] * u + uc[1] * v;
                tex.sample(tu, tv).1 >= 0.5
            }
            _ => true,
        }
    }

    fn walk(&self, o: V3, d: V3, mut tmax: f32, any: bool) -> Option<Hit> {
        let inv = V3::new(1.0 / d.x, 1.0 / d.y, 1.0 / d.z);
        let mut best: Option<Hit> = None;
        let mut stack = [0u32; 64];
        let mut sp = 0usize;
        if self.bvh.nodes.is_empty() || self.idx.is_empty() {
            return None;
        }
        if self.bvh.nodes[0].bounds.hit(o, inv, tmax).is_infinite() {
            return None;
        }
        stack[0] = 0;
        sp = sp + 1;
        while sp > 0 {
            sp -= 1;
            let node = &self.bvh.nodes[stack[sp] as usize];
            if node.count > 0 {
                let f = node.first as usize;
                for i in f..f + node.count as usize {
                    if let Some((t, u, v)) = self.tri_hit(i, o, d, tmax) {
                        if !self.solid(i, u, v) || (any && self.clear[i]) {
                            continue;
                        }
                        tmax = t;
                        best = Some(Hit { t, tri: i as u32, u, v });
                        if any {
                            return best;
                        }
                    }
                }
            } else {
                let l = node.first as usize;
                let (a, b) = (&self.bvh.nodes[l], &self.bvh.nodes[l + 1]);
                let ta = a.bounds.hit(o, inv, tmax);
                let tb = b.bounds.hit(o, inv, tmax);
                // Nearer child last, so it is popped first.
                let (near, nt, far, ft) =
                    if ta <= tb { (l, ta, l + 1, tb) } else { (l + 1, tb, l, ta) };
                if ft.is_finite() && sp < 64 {
                    stack[sp] = far as u32;
                    sp += 1;
                }
                if nt.is_finite() && sp < 64 {
                    stack[sp] = near as u32;
                    sp += 1;
                }
            }
        }
        best
    }

    pub fn intersect(&self, o: V3, d: V3) -> Option<Hit> {
        self.walk(o, d, f32::INFINITY, false)
    }

    pub fn occluded(&self, o: V3, d: V3, tmax: f32) -> bool {
        self.walk(o, d, tmax, true).is_some()
    }

    /// Geometric normal, shading normal and the surface at a hit.
    pub fn shade(&self, h: &Hit) -> (V3, V3, Surface) {
        let i = h.tri as usize;
        let tr = &self.tris[i];
        let ng = tr.e1.cross(tr.e2).norm();
        let [a, b, c] = self.idx[i];
        let w = 1.0 - h.u - h.v;
        let ns = match &self.nrm {
            Some(n) => (n[a as usize] * w + n[b as usize] * h.u + n[c as usize] * h.v).norm(),
            None => ng,
        };
        let m = &self.materials[self.mat[i] as usize];
        let mut s = Surface {
            base: m.base,
            roughness: m.roughness,
            metallic: m.metallic,
            emission: m.emission,
            coat: m.coat,
            alpha: 1.0,
            transmission: m.transmission,
        };
        if let (Some(tex), Some(uvs)) = (self.textures.get(m.texture as usize), &self.uv) {
            let (ua, ub, uc) = (uvs[a as usize], uvs[b as usize], uvs[c as usize]);
            let tu = ua[0] * w + ub[0] * h.u + uc[0] * h.v;
            let tv = ua[1] * w + ub[1] * h.u + uc[1] * h.v;
            let (col, alpha) = tex.sample(tu, tv);
            s.base = col;
            s.alpha = alpha;
            if let Some((r, mt, cc)) = tex.params_at(tu, tv) {
                s.roughness = r.max(0.02);
                s.metallic = mt;
                s.coat = cc;
            }
        }
        let ns = match (self.textures.get(m.texture as usize), &self.uv) {
            (Some(tex), Some(uvs)) if m.bump > 0.0 => {
                let (ua, ub, uc) = (uvs[a as usize], uvs[b as usize], uvs[c as usize]);
                let tu = ua[0] * w + ub[0] * h.u + uc[0] * h.v;
                let tv = ua[1] * w + ub[1] * h.u + uc[1] * h.v;
                // The surface's directions of u and v, from the triangle.
                let (dp1, dp2) = (tr.e1, tr.e2);
                let (du1, dv1) = (ub[0] - ua[0], ub[1] - ua[1]);
                let (du2, dv2) = (uc[0] - ua[0], uc[1] - ua[1]);
                let det = du1 * dv2 - du2 * dv1;
                if det.abs() < 1e-12 {
                    ns
                } else {
                    let r = 1.0 / det;
                    let t = (dp1 * dv2 - dp2 * dv1) * r;
                    let bt = (dp2 * du1 - dp1 * du2) * r;
                    let (eu, ev) = (1.0 / tex.w as f32, 1.0 / tex.h as f32);
                    let dhu = (tex.height_at(tu + eu, tv) - tex.height_at(tu - eu, tv)) / (2.0 * eu);
                    let dhv = (tex.height_at(tu, tv + ev) - tex.height_at(tu, tv - ev)) / (2.0 * ev);
                    let grad = t * (dhu * m.bump / t.dot(t).max(1e-12))
                        + bt * (dhv * m.bump / bt.dot(bt).max(1e-12));
                    (ns - grad).norm()
                }
            }
            _ => ns,
        };
        (ng, ns, s)
    }
}

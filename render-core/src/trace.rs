// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
//! The path tracer: camera rays, a layered surface model, lights, tone map.
//!
//! A surface is a base (diffuse, or metal by `metallic`) with GGX
//! specular, under an optional clear coat (`coat`, the gloss of solder
//! mask). Lights are directional discs of some angular size, like a
//! softbox far away: sampled directly with shadow rays and also found by
//! the bounce rays (so glossy surfaces show them), the two weighed by
//! multiple importance sampling. A sky lights the rest. A thin lens gives
//! depth of field, and a filter guided by the first hit's colour, normal
//! and depth takes the remaining grain out.

use crate::math::{basis, Rng, V3};
use crate::scene::{Scene, Surface};
use rayon::prelude::*;
use std::f32::consts::PI;

#[derive(Clone, Copy, Debug)]
pub struct Camera {
    pub pos: V3,
    pub target: V3,
    pub up: V3,
    pub fov_deg: f32,
    /// Orthographic when > 0: the view height in scene units.
    pub ortho_height: f32,
    /// Lens radius, scene units; 0 is a pinhole (all in focus).
    pub aperture: f32,
    /// Distance in focus; 0 is the target's.
    pub focus: f32,
}

#[derive(Clone, Copy, Debug)]
pub struct Light {
    /// Towards the light.
    pub dir: V3,
    pub radiance: V3,
    /// Half-angle of the light's disc, degrees; 0 is a hard shadow.
    pub angle_deg: f32,
}

#[derive(Clone, Debug)]
pub struct Settings {
    pub width: usize,
    pub height: usize,
    pub spp: u32,
    pub max_bounces: u32,
    pub exposure: f32,
    /// 0 sky, 1 transparent, 2 flat colour.
    pub background: u32,
    pub background_color: V3,
    pub sky_zenith: V3,
    pub sky_horizon: V3,
    pub sky_ground: V3,
    pub lights: Vec<Light>,
    pub seed: u64,
    pub denoise: bool,
}

const COAT_ROUGHNESS: f32 = 0.11;
const GLASS_IOR: f32 = 1.5;

/// Fresnel reflectance of a smooth dielectric; 1 past the critical angle.
fn fresnel_dielectric(cos_i: f32, eta: f32) -> f32 {
    let sin_t2 = eta * eta * (1.0 - cos_i * cos_i).max(0.0);
    if sin_t2 >= 1.0 {
        return 1.0;
    }
    let cos_t = (1.0 - sin_t2).sqrt();
    let rs = (eta * cos_i - cos_t) / (eta * cos_i + cos_t);
    let rp = (cos_i - eta * cos_t) / (cos_i + eta * cos_t);
    0.5 * (rs * rs + rp * rp)
}
const COAT_F0: f32 = 0.04;

fn ggx_d(nh: f32, a: f32) -> f32 {
    let a2 = a * a;
    let d = nh * nh * (a2 - 1.0) + 1.0;
    a2 / (PI * d * d).max(1e-12)
}

fn smith_g1(nv: f32, a: f32) -> f32 {
    let a2 = a * a;
    2.0 * nv / (nv + (a2 + (1.0 - a2) * nv * nv).sqrt()).max(1e-12)
}

fn schlick(f0: V3, c: f32) -> V3 {
    let k = (1.0 - c).clamp(0.0, 1.0).powi(5);
    f0 + (V3::splat(1.0) - f0) * k
}

fn schlick1(f0: f32, c: f32) -> f32 {
    f0 + (1.0 - f0) * (1.0 - c).clamp(0.0, 1.0).powi(5)
}

/// Microfacet specular, f * cos(wi), for roughness r (alpha = r^2).
fn spec(n: V3, wo: V3, wi: V3, r: f32, f0: V3) -> V3 {
    let a = (r * r).max(1e-3);
    let nl = n.dot(wi);
    let nv = n.dot(wo);
    if nl <= 0.0 || nv <= 0.0 {
        return V3::default();
    }
    let h = (wo + wi).norm();
    let d = ggx_d(n.dot(h).max(0.0), a);
    let g = smith_g1(nv, a) * smith_g1(nl, a);
    schlick(f0, wo.dot(h).max(0.0)) * (d * g / (4.0 * nv))
}

fn spec_pdf(n: V3, wo: V3, wi: V3, r: f32) -> f32 {
    let a = (r * r).max(1e-3);
    let h = (wo + wi).norm();
    let nh = n.dot(h).max(0.0);
    let vh = wo.dot(h).abs().max(1e-6);
    ggx_d(nh, a) * nh / (4.0 * vh)
}

fn sample_ggx(n: V3, wo: V3, r: f32, rng: &mut Rng) -> V3 {
    let a = (r * r).max(1e-3);
    let u1 = rng.f();
    let u2 = rng.f();
    let phi = 2.0 * PI * u1;
    let ct = ((1.0 - u2) / (1.0 + (a * a - 1.0) * u2)).sqrt();
    let st = (1.0 - ct * ct).max(0.0).sqrt();
    let (t, b) = basis(n);
    let h = (t * (st * phi.cos()) + b * (st * phi.sin()) + n * ct).norm();
    (h * (2.0 * wo.dot(h)) - wo).norm()
}

fn sample_cosine(n: V3, rng: &mut Rng) -> V3 {
    let u1 = rng.f();
    let u2 = rng.f();
    let r = u1.sqrt();
    let phi = 2.0 * PI * u2;
    let (t, b) = basis(n);
    (t * (r * phi.cos()) + b * (r * phi.sin()) + n * (1.0 - u1).max(0.0).sqrt()).norm()
}

/// The whole surface, f * cos(wi).
fn bsdf(s: &Surface, n: V3, wo: V3, wi: V3) -> V3 {
    let nl = n.dot(wi);
    let nv = n.dot(wo);
    if nl <= 0.0 || nv <= 0.0 {
        return V3::default();
    }
    let f0 = V3::splat(0.04).lerp(s.base, s.metallic);
    // Under a coat the base is pigment in the coat's resin: its own
    // gloss is the coat's, so its specular fades out as the coat comes in.
    let mut f = spec(n, wo, wi, s.roughness, f0) * (1.0 - s.coat * (1.0 - s.metallic));
    let diffuse = s.base * ((1.0 - s.metallic) * (1.0 - 0.04) * nl / PI);
    f += diffuse;
    if s.coat > 0.0 {
        let fc = schlick1(COAT_F0, nv) * s.coat;
        f = f * (1.0 - fc) * (1.0 - schlick1(COAT_F0, nl) * s.coat)
            + spec(n, wo, wi, COAT_ROUGHNESS, V3::splat(COAT_F0)) * s.coat;
    }
    f
}

struct Lobes {
    coat: f32,
    spec: f32,
    diff: f32,
}

fn lobes(s: &Surface) -> Lobes {
    let coat = if s.coat > 0.0 { 0.2 * s.coat } else { 0.0 };
    let spec_share = 0.25 + 0.75 * s.metallic;
    let rest = 1.0 - coat;
    Lobes { coat, spec: rest * spec_share, diff: rest * (1.0 - spec_share) }
}

fn pdf(s: &Surface, l: &Lobes, n: V3, wo: V3, wi: V3) -> f32 {
    let nl = n.dot(wi).max(0.0);
    let mut p = l.diff * nl / PI + l.spec * spec_pdf(n, wo, wi, s.roughness);
    if l.coat > 0.0 {
        p += l.coat * spec_pdf(n, wo, wi, COAT_ROUGHNESS);
    }
    p
}

impl Settings {
    fn sky(&self, d: V3) -> V3 {
        if d.z >= 0.0 {
            self.sky_horizon.lerp(self.sky_zenith, d.z.sqrt())
        } else {
            self.sky_horizon.lerp(self.sky_ground, (-d.z).sqrt())
        }
    }
}

impl Light {
    fn cos_max(&self) -> f32 {
        self.angle_deg.to_radians().cos()
    }
    /// Solid angle of the disc; 0 for a hard light.
    fn solid_angle(&self) -> f32 {
        if self.angle_deg <= 0.0 {
            0.0
        } else {
            2.0 * PI * (1.0 - self.cos_max())
        }
    }
}

fn sample_cone(dir: V3, angle_deg: f32, rng: &mut Rng) -> V3 {
    if angle_deg <= 0.0 {
        return dir;
    }
    let cmax = angle_deg.to_radians().cos();
    let ct = 1.0 - rng.f() * (1.0 - cmax);
    let st = (1.0 - ct * ct).max(0.0).sqrt();
    let phi = 2.0 * PI * rng.f();
    let (t, b) = basis(dir);
    (t * (st * phi.cos()) + b * (st * phi.sin()) + dir * ct).norm()
}

/// What the camera ray met first, for the denoiser.
#[derive(Clone, Copy, Default)]
struct First {
    albedo: V3,
    normal: V3,
    depth: f32,
}

/// Radiance along one camera ray, whether it hit anything, and its first hit.
fn radiance(scene: &Scene, st: &Settings, mut o: V3, mut d: V3, eps: f32, rng: &mut Rng) -> (V3, bool, First) {
    let mut out = V3::default();
    let mut through = V3::splat(1.0);
    let mut primary_hit = false;
    let mut first = First { albedo: V3::splat(1.0), normal: V3::default(), depth: f32::INFINITY };
    let mut bounce = 0u32;
    // The pdf of the last bounce's direction, for weighing lights it finds.
    let mut last_pdf = 0.0f32;
    loop {
        let hit = match scene.intersect(o, d) {
            Some(h) => h,
            None => {
                if bounce == 0 {
                    if st.background == 0 {
                        out += through.mul(st.sky(d));
                    } else if st.background == 2 {
                        out += st.background_color;
                    }
                } else {
                    out += through.mul(st.sky(d));
                    for light in &st.lights {
                        let sa = light.solid_angle();
                        if sa <= 0.0 || d.dot(light.dir) < light.cos_max() {
                            continue;
                        }
                        let pl = 1.0 / sa;
                        let w = last_pdf * last_pdf / (last_pdf * last_pdf + pl * pl);
                        out += through.mul(light.radiance * (w / sa));
                    }
                }
                break;
            }
        };
        let p = o + d * hit.t;
        let (ng, ns, s) = scene.shade(&hit);
        if s.transmission > 0.0 && rng.f() < s.transmission {
            // Clear: reflect or refract by Fresnel, smooth. The mesh's
            // normal points out, so it says which way the ray is going.
            if bounce == 0 {
                primary_hit = true;
                let nf = if ng.dot(d) < 0.0 { ng } else { -ng };
                first = First { albedo: s.base, normal: nf, depth: hit.t };
            }
            let outward = if ns.dot(ng) < 0.0 { -ns } else { ns };
            let entering = d.dot(ng) < 0.0;
            let n = if entering { outward } else { -outward };
            let gn = if entering { ng } else { -ng };
            let eta = if entering { 1.0 / GLASS_IOR } else { GLASS_IOR };
            let cos_i = (-d.dot(n)).clamp(0.0, 1.0);
            let f = fresnel_dielectric(cos_i, eta);
            if rng.f() < f {
                d = (d + n * (2.0 * cos_i)).norm();
                o = p + gn * eps;
            } else {
                let k = 1.0 - eta * eta * (1.0 - cos_i * cos_i);
                d = (d * eta + n * (eta * cos_i - k.max(0.0).sqrt())).norm();
                o = p - gn * eps;
                through = through.mul(s.base);
            }
            // A mirror-like event: a light it finds counts in full.
            last_pdf = 1e6;
            if bounce >= st.max_bounces + 4 {
                break;
            }
            bounce += 1;
            continue;
        }
        // Face the normals towards the incoming ray.
        let wo = -d;
        let ng = if ng.dot(wo) < 0.0 { -ng } else { ng };
        let mut n = if ns.dot(ng) < 0.0 { -ns } else { ns };
        if n.dot(wo) <= 0.0 {
            n = ng;
        }
        if bounce == 0 {
            primary_hit = true;
            // The geometric normal guides the denoiser: relief would stop it.
            first = First { albedo: s.base, normal: ng, depth: hit.t };
        }
        out += through.mul(s.emission);
        let lob = lobes(&s);
        let origin = p + ng * eps;
        for light in &st.lights {
            let l = sample_cone(light.dir, light.angle_deg, rng);
            if n.dot(l) <= 0.0 || ng.dot(l) <= 0.0 {
                continue;
            }
            if scene.occluded(origin, l, f32::INFINITY) {
                continue;
            }
            let sa = light.solid_angle();
            // A bounce from here can find this light too: share it.
            let w = if sa > 0.0 && bounce < st.max_bounces {
                let pl = 1.0 / sa;
                let pb = pdf(&s, &lob, n, wo, l);
                pl * pl / (pl * pl + pb * pb)
            } else {
                1.0
            };
            out += through.mul(bsdf(&s, n, wo, l)).mul(light.radiance) * w;
        }
        if bounce >= st.max_bounces {
            break;
        }
        // Pick a lobe, sample a direction, weight by the mixed pdf.
        let x = rng.f();
        let wi = if x < lob.coat {
            sample_ggx(n, wo, COAT_ROUGHNESS, rng)
        } else if x < lob.coat + lob.spec {
            sample_ggx(n, wo, s.roughness, rng)
        } else {
            sample_cosine(n, rng)
        };
        if wi.dot(ng) <= 0.0 {
            break;
        }
        let pd = pdf(&s, &lob, n, wo, wi);
        if pd <= 1e-8 {
            break;
        }
        through = through.mul(bsdf(&s, n, wo, wi)) / pd;
        last_pdf = pd;
        if bounce >= 3 {
            let q = through.max_comp().clamp(0.05, 0.95);
            if rng.f() > q {
                break;
            }
            through = through / q;
        }
        o = origin;
        d = wi;
        bounce += 1;
    }
    (out, primary_hit, first)
}

fn lum(c: V3) -> f32 {
    0.2126 * c.x + 0.7152 * c.y + 0.0722 * c.z
}

fn floor_albedo(a: V3) -> V3 {
    V3::new(a.x.max(0.02), a.y.max(0.02), a.z.max(0.02))
}

/// Edge-avoiding a-trous filter (five passes of a 5x5 B-spline kernel at
/// growing steps) on the light the surfaces receive: colour divided by
/// the first hit's albedo, so texture detail never blurs. Normals, depth,
/// albedo and the per-pixel noise estimate stop it at edges.
fn denoise(col: &mut [V3], var: &mut [f32], f: &[First], w: usize, h: usize) {
    let k = [1.0 / 16.0, 1.0 / 4.0, 3.0 / 8.0, 1.0 / 4.0, 1.0 / 16.0];
    let mut irr: Vec<V3> = col
        .iter()
        .zip(f)
        .map(|(&c, fi)| {
            let a = floor_albedo(fi.albedo);
            V3::new(c.x / a.x, c.y / a.y, c.z / a.z)
        })
        .collect();
    for pass in 0..5 {
        let step = 1isize << pass;
        let src = irr.clone();
        let vsrc = var.to_vec();
        let rows: Vec<(Vec<V3>, Vec<f32>)> = (0..h)
            .into_par_iter()
            .map(|y| {
                let mut ro = vec![V3::default(); w];
                let mut rv = vec![0.0f32; w];
                for x in 0..w {
                    let i = y * w + x;
                    let fi = &f[i];
                    if !fi.depth.is_finite() {
                        ro[x] = src[i];
                        rv[x] = vsrc[i];
                        continue;
                    }
                    // Compare in colour, where the noise estimate lives.
                    let ai = floor_albedo(fi.albedo);
                    let li = lum(src[i].mul(ai));
                    let sig = 4.0 * vsrc[i].max(0.0).sqrt() + 1e-3;
                    let mut acc = V3::default();
                    let mut accv = 0.0f32;
                    let mut wsum = 0.0f32;
                    for (jy, ky) in k.iter().enumerate() {
                        let yy = y as isize + (jy as isize - 2) * step;
                        if yy < 0 || yy >= h as isize {
                            continue;
                        }
                        for (jx, kx) in k.iter().enumerate() {
                            let xx = x as isize + (jx as isize - 2) * step;
                            if xx < 0 || xx >= w as isize {
                                continue;
                            }
                            let j = yy as usize * w + xx as usize;
                            let fj = &f[j];
                            if !fj.depth.is_finite() {
                                continue;
                            }
                            let wn = fi.normal.dot(fj.normal).max(0.0).powi(64);
                            let wd = (-(fi.depth - fj.depth).abs()
                                / (fi.depth * 0.004 * step as f32 + 1e-6))
                                .exp();
                            let da = fi.albedo - fj.albedo;
                            let wa = (-(da.x.abs() + da.y.abs() + da.z.abs()) * 12.0).exp();
                            let wl = (-(li - lum(src[j].mul(ai))).abs() / sig).exp();
                            let wt = kx * ky * wn * wd * wa * wl;
                            acc += src[j] * wt;
                            accv += vsrc[j] * wt * wt;
                            wsum += wt;
                        }
                    }
                    if wsum > 0.0 {
                        ro[x] = acc / wsum;
                        rv[x] = accv / (wsum * wsum);
                    } else {
                        ro[x] = src[i];
                        rv[x] = vsrc[i];
                    }
                }
                (ro, rv)
            })
            .collect();
        for (y, (ro, rv)) in rows.into_iter().enumerate() {
            irr[y * w..(y + 1) * w].copy_from_slice(&ro);
            var[y * w..(y + 1) * w].copy_from_slice(&rv);
        }
    }
    for i in 0..col.len() {
        if f[i].depth.is_finite() {
            col[i] = irr[i].mul(floor_albedo(f[i].albedo));
        }
    }
}

fn aces(x: f32) -> f32 {
    let (a, b, c, d, e) = (2.51, 0.03, 2.43, 0.59, 0.14);
    ((x * (a * x + b)) / (x * (c * x + d) + e)).clamp(0.0, 1.0)
}

fn to_srgb(c: f32) -> u8 {
    let c = c.clamp(0.0, 1.0);
    let s = if c <= 0.0031308 { c * 12.92 } else { 1.055 * c.powf(1.0 / 2.4) - 0.055 };
    (s * 255.0 + 0.5) as u8
}

/// Render to RGBA8, rows top to bottom.
pub fn render(scene: &Scene, cam: &Camera, st: &Settings) -> Vec<u8> {
    let (w, h) = (st.width, st.height);
    let fwd = (cam.target - cam.pos).norm();
    let right = fwd.cross(cam.up).norm();
    let up = right.cross(fwd).norm();
    let aspect = w as f32 / h as f32;
    let half_h = if cam.ortho_height > 0.0 {
        cam.ortho_height / 2.0
    } else {
        (cam.fov_deg.to_radians() / 2.0).tan()
    };
    let half_w = half_h * aspect;
    let focus = if cam.focus > 0.0 { cam.focus } else { (cam.target - cam.pos).length() };
    let b = scene.bounds();
    let size = (b.hi - b.lo).length().max(1e-3);
    let eps = size * 2e-5;
    let spp = st.spp.max(1);
    // Per pixel: mean colour, luminance variance of the mean, coverage, first hit.
    let rows: Vec<Vec<(V3, f32, f32, First)>> = (0..h)
        .into_par_iter()
        .map(|y| {
            let mut row = Vec::with_capacity(w);
            for x in 0..w {
                let mut rng = Rng::new(
                    st.seed ^ ((y * w + x) as u64).wrapping_mul(0x9E3779B97F4A7C15),
                    (y * w + x) as u64,
                );
                let mut acc = V3::default();
                let mut sq = 0.0f32;
                let mut albedo = V3::default();
                let mut normal = V3::default();
                let mut depth = f32::INFINITY;
                let mut hits = 0u32;
                for _ in 0..spp {
                    let sx = ((x as f32 + rng.f()) / w as f32) * 2.0 - 1.0;
                    let sy = 1.0 - ((y as f32 + rng.f()) / h as f32) * 2.0;
                    let (o, d) = if cam.ortho_height > 0.0 {
                        (cam.pos + right * (sx * half_w) + up * (sy * half_h), fwd)
                    } else {
                        let d = (fwd + right * (sx * half_w) + up * (sy * half_h)).norm();
                        if cam.aperture > 0.0 {
                            // A point on the lens, aimed at the same point
                            // on the plane in focus.
                            let fp = cam.pos + d * (focus / d.dot(fwd));
                            let r = cam.aperture * rng.f().sqrt();
                            let a = 2.0 * PI * rng.f();
                            let o = cam.pos + right * (r * a.cos()) + up * (r * a.sin());
                            (o, (fp - o).norm())
                        } else {
                            (cam.pos, d)
                        }
                    };
                    let (l, hit, first) = radiance(scene, st, o, d, eps, &mut rng);
                    // Fireflies: clamp a single sample's contribution.
                    let m = l.max_comp();
                    let l = if m > 20.0 { l * (20.0 / m) } else { l };
                    acc += l;
                    let y_ = lum(l);
                    sq += y_ * y_;
                    if hit {
                        hits += 1;
                        albedo += first.albedo;
                        normal += first.normal;
                        depth = depth.min(first.depth);
                    }
                }
                let n = spp as f32;
                let mean = acc / n;
                let var = (sq / n - lum(mean) * lum(mean)).max(0.0) / n;
                let first = if hits > 0 {
                    First { albedo: albedo / hits as f32, normal: normal.norm(), depth }
                } else {
                    First { albedo: V3::splat(1.0), normal: V3::default(), depth: f32::INFINITY }
                };
                row.push((mean, var, hits as f32 / n, first));
            }
            row
        })
        .collect();
    let mut col: Vec<V3> = Vec::with_capacity(w * h);
    let mut var: Vec<f32> = Vec::with_capacity(w * h);
    let mut cov: Vec<f32> = Vec::with_capacity(w * h);
    let mut feats: Vec<First> = Vec::with_capacity(w * h);
    for row in rows {
        for (c, v, a, f) in row {
            col.push(c);
            var.push(v);
            cov.push(a);
            feats.push(f);
        }
    }
    if st.denoise {
        denoise(&mut col, &mut var, &feats, w, h);
    }
    let mut img = vec![0u8; w * h * 4];
    for i in 0..w * h {
        let a = if st.background == 1 { cov[i] } else { 1.0 };
        let c = col[i] * st.exposure;
        // A transparent background keeps colour unpremultiplied.
        let c = if st.background == 1 && a > 0.0 { c / a } else { c };
        img[4 * i] = to_srgb(aces(c.x));
        img[4 * i + 1] = to_srgb(aces(c.y));
        img[4 * i + 2] = to_srgb(aces(c.z));
        img[4 * i + 3] = (a * 255.0 + 0.5) as u8;
    }
    img
}

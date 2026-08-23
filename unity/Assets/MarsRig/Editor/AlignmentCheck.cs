using System.IO;
using UnityEditor;
using UnityEngine;

namespace MarsRig.EditorTools
{
    /// <summary>
    /// Mars Rig > Check Camera Alignment
    ///
    /// BUILD_SPEC 5.1: "Camera 1 and Camera 2 must be pixel-identical... Verify
    /// by overlaying two screenshots and confirming rocks land in the same
    /// place. If this fails, every number downstream is wrong. Do this before
    /// anything else and show me the result."
    ///
    /// DualCapture renders both passes from one camera in one frame, so the
    /// classic ways two cameras drift apart cannot happen here. This check is
    /// what confirms that rather than assuming it, and it catches the failures
    /// that survive the single-camera design:
    ///
    ///   - a label material that is not unlit, so the flat colour shifts and
    ///     the scorer assigns it to a neighbouring class
    ///   - fog or post-processing tinting the label pass
    ///   - an object with no SemanticLabel, invisible in the answer key
    ///   - rocks too small or too distant for obstacle IoU to mean anything
    ///
    /// It writes three PNGs and prints one number. Put that number in the
    /// write-up: "we measured our own ground truth" is the answer to the
    /// question a judge asks next.
    /// </summary>
    public static class AlignmentCheck
    {
        const string OutDir = "AlignmentCheck";
        const int EdgeThreshold = 40;      // Sobel magnitude counted as an edge
        const int SearchRadius = 2;        // px of slack allowed when matching

        [MenuItem("Mars Rig/Check Camera Alignment")]
        public static void Run()
        {
            if (!Application.isPlaying)
            {
                EditorUtility.DisplayDialog(
                    "Enter play mode first",
                    "The check renders through the live camera, so the scene has to " +
                    "be running. Press Play, then run this again.",
                    "OK");
                return;
            }

            var capture = Object.FindObjectOfType<DualCapture>();
            if (capture == null)
            {
                Debug.LogError("[MarsRig] no DualCapture in the scene. " +
                               "Run Mars Rig > Build Scored Scene first.");
                return;
            }

            var cam = capture.GetComponent<Camera>();
            int w = capture.width, h = capture.height;

            var rgb = Grab(cam, capture, w, h, false);
            var labels = Grab(cam, capture, w, h, true);

            Directory.CreateDirectory(OutDir);
            File.WriteAllBytes(Path.Combine(OutDir, "camera1_rgb.png"), rgb.EncodeToPNG());
            File.WriteAllBytes(Path.Combine(OutDir, "camera2_labels.png"), labels.EncodeToPNG());
            File.WriteAllBytes(Path.Combine(OutDir, "overlay.png"),
                               Overlay(rgb, labels).EncodeToPNG());

            Report(rgb, labels, w, h);
            Debug.Log($"[MarsRig] wrote {Path.GetFullPath(OutDir)} -- " +
                      "open overlay.png and confirm rocks land in the same place.");
        }

        /// <summary>Render one pass through DualCapture's own code path.</summary>
        static Texture2D Grab(Camera cam, DualCapture capture, int w, int h, bool labelPass)
        {
            var rt = new RenderTexture(w, h, 24, RenderTextureFormat.ARGB32);
            rt.filterMode = FilterMode.Point;
            var tex = new Texture2D(w, h, TextureFormat.RGB24, false);

            var prevTarget = cam.targetTexture;
            var prevClear = cam.clearFlags;
            var prevBg = cam.backgroundColor;
            var prevFog = RenderSettings.fog;

            if (labelPass)
            {
                capture.SwapToFlat();
                cam.clearFlags = CameraClearFlags.SolidColor;
                cam.backgroundColor = MarsClasses.ColorOf(MarsClass.Sky);
                RenderSettings.fog = false;
            }

            cam.targetTexture = rt;
            cam.Render();
            var prevActive = RenderTexture.active;
            RenderTexture.active = rt;
            tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
            tex.Apply(false);
            RenderTexture.active = prevActive;

            cam.targetTexture = prevTarget;
            if (labelPass)
            {
                capture.RestoreMaterials();
                cam.clearFlags = prevClear;
                cam.backgroundColor = prevBg;
                RenderSettings.fog = prevFog;
            }
            rt.Release();
            return tex;
        }

        static void Report(Texture2D rgb, Texture2D labels, int w, int h)
        {
            var labelPixels = labels.GetPixels32();
            var rgbPixels = rgb.GetPixels32();

            // 1. Class coverage. Obstacle below ~1% of frame means the rocks are
            //    too small or too far for obstacle IoU to carry any signal, and
            //    the sweep will look flat for a reason that is not the model's.
            int drivable = 0, obstacle = 0, sky = 0, unlabelled = 0, offPalette = 0;
            foreach (var p in labelPixels)
            {
                switch (NearestClass(p, out int distance))
                {
                    case MarsClass.Drivable: drivable++; break;
                    case MarsClass.Obstacle: obstacle++; break;
                    case MarsClass.Sky: sky++; break;
                    default: unlabelled++; break;
                }
                // A label pixel far from every palette colour means something
                // tinted the label pass -- lighting, fog, or a non-unlit material.
                if (distance > 40 * 40) offPalette++;
            }

            int total = w * h;
            Debug.Log(string.Format(
                "[MarsRig] label coverage: drivable {0:P1}  obstacle {1:P1}  sky {2:P1}  " +
                "unlabelled {3:P1}",
                drivable / (float)total, obstacle / (float)total,
                sky / (float)total, unlabelled / (float)total));

            if (offPalette > total * 0.001f)
            {
                Debug.LogError(string.Format(
                    "[MarsRig] {0:P2} of label pixels are not a palette colour. The label " +
                    "pass is being tinted -- check for fog, post-processing, or a label " +
                    "material that is not Unlit/Color. The scorer will assign these to " +
                    "whichever class is nearest and report a plausible, wrong IoU.",
                    offPalette / (float)total));
            }

            if (obstacle < total * 0.01f)
            {
                Debug.LogWarning(string.Format(
                    "[MarsRig] obstacle is only {0:P2} of the frame. Obstacle IoU will be " +
                    "noise-dominated. Move rocks closer to the path or make them larger.",
                    obstacle / (float)total));
            }

            // 2. Edge agreement. Every label boundary should sit on a real edge
            //    in the RGB. This is the number that says the two views describe
            //    the same geometry.
            var rgbEdges = Edges(rgbPixels, w, h);
            var labelEdges = Edges(labelPixels, w, h);

            int labelEdgeCount = 0, matched = 0;
            for (int y = SearchRadius; y < h - SearchRadius; y++)
            {
                for (int x = SearchRadius; x < w - SearchRadius; x++)
                {
                    if (!labelEdges[y * w + x]) continue;
                    labelEdgeCount++;
                    if (HasEdgeNear(rgbEdges, w, h, x, y)) matched++;
                }
            }

            float agreement = labelEdgeCount > 0 ? matched / (float)labelEdgeCount : 0f;
            var verdict = agreement >= 0.85f ? "PASS"
                        : agreement >= 0.70f ? "MARGINAL" : "FAIL";
            var line = string.Format(
                "[MarsRig] ALIGNMENT {0}: {1:P1} of label edges sit on an RGB edge " +
                "(within {2}px, n={3})", verdict, agreement, SearchRadius, labelEdgeCount);

            if (agreement >= 0.85f) Debug.Log(line);
            else if (agreement >= 0.70f) Debug.LogWarning(line + " -- investigate before sweeping.");
            else Debug.LogError(line + " -- STOP. Every number downstream is wrong until this passes.");
        }

        static MarsClass NearestClass(Color32 p, out int distance)
        {
            var best = MarsClass.Unlabelled;
            distance = int.MaxValue;
            foreach (var kv in MarsClasses.Palette)
            {
                int dr = p.r - kv.Value.r, dg = p.g - kv.Value.g, db = p.b - kv.Value.b;
                int d = dr * dr + dg * dg + db * db;
                if (d >= distance) continue;
                distance = d;
                best = kv.Key;
            }
            return best;
        }

        static bool[] Edges(Color32[] px, int w, int h)
        {
            var grey = new float[w * h];
            for (int i = 0; i < px.Length; i++)
                grey[i] = 0.299f * px[i].r + 0.587f * px[i].g + 0.114f * px[i].b;

            var edges = new bool[w * h];
            for (int y = 1; y < h - 1; y++)
            {
                for (int x = 1; x < w - 1; x++)
                {
                    int i = y * w + x;
                    float gx =
                        -grey[i - w - 1] - 2 * grey[i - 1] - grey[i + w - 1]
                        + grey[i - w + 1] + 2 * grey[i + 1] + grey[i + w + 1];
                    float gy =
                        -grey[i - w - 1] - 2 * grey[i - w] - grey[i - w + 1]
                        + grey[i + w - 1] + 2 * grey[i + w] + grey[i + w + 1];
                    edges[i] = Mathf.Sqrt(gx * gx + gy * gy) > EdgeThreshold;
                }
            }
            return edges;
        }

        static bool HasEdgeNear(bool[] edges, int w, int h, int x, int y)
        {
            for (int dy = -SearchRadius; dy <= SearchRadius; dy++)
            {
                for (int dx = -SearchRadius; dx <= SearchRadius; dx++)
                {
                    if (edges[(y + dy) * w + (x + dx)]) return true;
                }
            }
            return false;
        }

        static Texture2D Overlay(Texture2D rgb, Texture2D labels)
        {
            var a = rgb.GetPixels32();
            var b = labels.GetPixels32();
            var outPx = new Color32[a.Length];
            for (int i = 0; i < a.Length; i++)
            {
                outPx[i] = new Color32(
                    (byte)((a[i].r + b[i].r) / 2),
                    (byte)((a[i].g + b[i].g) / 2),
                    (byte)((a[i].b + b[i].b) / 2), 255);
            }
            var tex = new Texture2D(rgb.width, rgb.height, TextureFormat.RGB24, false);
            tex.SetPixels32(outPx);
            tex.Apply(false);
            return tex;
        }
    }
}

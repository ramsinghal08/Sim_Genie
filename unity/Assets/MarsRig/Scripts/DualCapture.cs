using System.Collections;
using System.Collections.Generic;
using RosMessageTypes.Sensor;
using RosMessageTypes.Std;
using Unity.Robotics.ROSTCPConnector;
using UnityEngine;

namespace MarsRig
{
    /// <summary>
    /// Renders the RGB view and the semantic labels from ONE camera, in one
    /// frame, and publishes both under ONE timestamp.
    ///
    /// WHY ONE CAMERA AND NOT TWO
    /// --------------------------
    /// the design requirements says Camera 1 and Camera 2 must be pixel-identical --
    /// same transform, FOV, resolution and frame -- and that if this fails
    /// every number downstream is wrong. Two camera objects can drift apart in
    /// six different ways: someone nudges a transform, changes an FOV, edits
    /// one aspect ratio, or the two render on different frames while the rover
    /// is moving. None of those throw.
    ///
    /// One camera rendered twice cannot drift. The transform, the FOV and the
    /// projection are literally the same object, and both renders happen inside
    /// one LateUpdate, so the rover has not moved between them. The alignment
    /// check in Editor/AlignmentCheck.cs then verifies the result rather than
    /// being the only thing standing between us and silent garbage.
    ///
    /// Perception never sees the label topic. Only scoring_node subscribes to
    /// it.
    /// </summary>
    [RequireComponent(typeof(Camera))]
    public class DualCapture : MonoBehaviour
    {
        [Header("Topics")]
        [Tooltip("Raw sensor_msgs/Image, to match every subscriber in the rig. " +
                 "With rgbAsJpeg the CompressedImage goes to <rgbTopic>/compressed " +
                 "instead, and x2_bridge needs input_compressed:=true to follow it.")]
        public string rgbTopic = "/unity/camera/image_raw";
        public string labelTopic = "/unity/labels";
        public string frameId = "unity_cam";

        [Header("Capture")]
        [Tooltip("X2's native source pacing. Do not change without reading the design requirements")]
        public int rgbFps = 24;

        [Tooltip("The scorer caps at 10 Hz, so publishing labels faster is pure bandwidth.")]
        public int labelFps = 10;

        public int width = 832;
        public int height = 512;

        [Tooltip("Publish the RGB view as a JPEG CompressedImage on " +
                 "<rgbTopic>/compressed instead of raw Image. Off by default: " +
                 "nothing in the rig subscribes to a compressed topic unless it " +
                 "is told to, and a topic that exists on only one side of the " +
                 "graph is silent -- no frames, no error, an empty panel pane. " +
                 "Turning this on means setting x2_bridge's input_compressed AND " +
                 "the panel's transport to match. It costs bandwidth to leave " +
                 "off: raw rgb8 at 832x512x24fps is ~30 MiB/s, so drop rgbFps or " +
                 "the resolution rather than assuming the link carries it.")]
        public bool rgbAsJpeg = false;

        [Range(1, 100)]
        [Tooltip("rgbAsJpeg only. Labels are NEVER JPEG -- see labelAsPng.")]
        public int jpegQuality = 80;

        [Tooltip("PNG is lossless, so classes survive it exactly, and a flat-colour " +
                 "label image compresses enormously. Raw matches the design requirements as " +
                 "written; set scoring_node's ground_truth_compressed to match.")]
        public bool labelAsPng = false;

        Camera _cam;
        RenderTexture _rgbRt, _labelRt;
        Texture2D _rgbTex, _labelTex;
        ROSConnection _ros;

        // Renderer -> the flat material its class paints. Built once: walking
        // the scene graph per frame at 24 fps is the kind of thing that quietly
        // halves the frame rate and gets blamed on the network.
        readonly List<Renderer> _renderers = new List<Renderer>();
        readonly List<Material[]> _originals = new List<Material[]>();
        readonly List<Material[]> _flats = new List<Material[]>();
        readonly Dictionary<MarsClass, Material> _flatByClass =
            new Dictionary<MarsClass, Material>();

        void Start()
        {
            _cam = GetComponent<Camera>();
            _ros = ROSConnection.GetOrCreateInstance();
            if (rgbAsJpeg) _ros.RegisterPublisher<CompressedImageMsg>(rgbTopic + "/compressed");
            else _ros.RegisterPublisher<ImageMsg>(rgbTopic);
            if (labelAsPng) _ros.RegisterPublisher<CompressedImageMsg>(labelTopic + "/compressed");
            else _ros.RegisterPublisher<ImageMsg>(labelTopic);

            _rgbRt = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32);
            _labelRt = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32);
            // No mipmaps and no filtering on the label target: a filtered label
            // pixel is a blend of two class colours, which is a class that does
            // not exist.
            _labelRt.filterMode = FilterMode.Point;
            _rgbTex = new Texture2D(width, height, TextureFormat.RGB24, false);
            _labelTex = new Texture2D(width, height, TextureFormat.RGB24, false);

            BuildMaterialCache();
            StartCoroutine(CaptureLoop());
        }

        /// <summary>Re-scan the scene. Call after spawning or deleting objects.</summary>
        public void BuildMaterialCache()
        {
            _renderers.Clear();
            _originals.Clear();
            _flats.Clear();

            foreach (var flat in _flatByClass.Values) Destroy(flat);
            _flatByClass.Clear();

            foreach (MarsClass c in System.Enum.GetValues(typeof(MarsClass)))
            {
                // Unlit so the label colour is exactly the palette value --
                // any lighting response would shift it toward a neighbouring
                // class and the scorer would never know.
                var m = new Material(Shader.Find("Unlit/Color"));
                m.color = MarsClasses.ColorOf(c);
                _flatByClass[c] = m;
            }

            foreach (var r in FindObjectsOfType<Renderer>())
            {
                if (!r.enabled) continue;
                var label = r.GetComponentInParent<SemanticLabel>();
                // Untagged renders as Unlabelled, which the scorer ignores. A
                // forgotten object costs us a hole in the answer key, never a
                // wrong answer counted as right.
                var cls = label != null ? label.marsClass : MarsClass.Unlabelled;

                var originals = r.sharedMaterials;
                var flats = new Material[originals.Length];
                for (int i = 0; i < flats.Length; i++) flats[i] = _flatByClass[cls];

                _renderers.Add(r);
                _originals.Add(originals);
                _flats.Add(flats);
            }
            Debug.Log($"[MarsRig] label cache: {_renderers.Count} renderers");
        }

        IEnumerator CaptureLoop()
        {
            var rgbPeriod = 1f / Mathf.Max(1, rgbFps);
            var labelPeriod = 1f / Mathf.Max(1, labelFps);
            var nextLabel = 0f;

            while (true)
            {
                // End of frame so the scene is fully updated -- capturing
                // mid-frame gives a rover pose that no published image matches.
                yield return new WaitForEndOfFrame();

                var header = RosTime.Header(frameId);

                RenderTo(_rgbRt, _rgbTex, false);
                PublishRgb(header);

                // Both images carry the SAME header, so the scorer's
                // ApproximateTimeSynchronizer pairs the render with the labels
                // it was generated from, not with a neighbouring frame.
                if (Time.time >= nextLabel)
                {
                    nextLabel = Time.time + labelPeriod;
                    RenderTo(_labelRt, _labelTex, true);
                    PublishLabels(header);
                }

                yield return new WaitForSeconds(rgbPeriod);
            }
        }

        void RenderTo(RenderTexture rt, Texture2D tex, bool labelPass)
        {
            var prevTarget = _cam.targetTexture;
            var prevClear = _cam.clearFlags;
            var prevBg = _cam.backgroundColor;
            var prevFog = RenderSettings.fog;

            if (labelPass)
            {
                SwapToFlat();
                // Anything the geometry does not cover is sky by definition.
                _cam.clearFlags = CameraClearFlags.SolidColor;
                _cam.backgroundColor = MarsClasses.ColorOf(MarsClass.Sky);
                // Fog tints the flat colours toward the fog colour, which is a
                // silent class change on distant objects -- exactly where the
                // interesting rocks are.
                RenderSettings.fog = false;
            }

            _cam.targetTexture = rt;
            _cam.Render();

            var prevActive = RenderTexture.active;
            RenderTexture.active = rt;
            tex.ReadPixels(new Rect(0, 0, rt.width, rt.height), 0, 0);
            tex.Apply(false);
            RenderTexture.active = prevActive;

            _cam.targetTexture = prevTarget;
            if (labelPass)
            {
                RestoreMaterials();
                _cam.clearFlags = prevClear;
                _cam.backgroundColor = prevBg;
                RenderSettings.fog = prevFog;
            }
        }

        /// <summary>Public so AlignmentCheck can drive the same code path
        /// directly. SendMessage would do it too, but a typo there fails
        /// silently, which is the one thing this rig cannot afford.</summary>
        public void SwapToFlat()
        {
            for (int i = 0; i < _renderers.Count; i++)
            {
                if (_renderers[i] == null) continue;
                _renderers[i].sharedMaterials = _flats[i];
            }
        }

        public void RestoreMaterials()
        {
            for (int i = 0; i < _renderers.Count; i++)
            {
                if (_renderers[i] == null) continue;
                _renderers[i].sharedMaterials = _originals[i];
            }
        }

        void PublishRgb(HeaderMsg header)
        {
            var flipped = FlipVertically(_rgbTex);
            if (rgbAsJpeg)
            {
                _ros.Publish(rgbTopic + "/compressed", new CompressedImageMsg
                {
                    header = header,
                    format = "jpeg",
                    data = ImageConversion.EncodeToJPG(flipped, jpegQuality),
                });
                return;
            }

            // RGB24 is three tightly packed bytes per pixel, which is exactly
            // sensor_msgs rgb8 with step = width * 3. No conversion, no copy
            // beyond the one GetRawTextureData already makes.
            _ros.Publish(rgbTopic, new ImageMsg
            {
                header = header,
                height = (uint)height,
                width = (uint)width,
                encoding = "rgb8",
                is_bigendian = 0,
                step = (uint)(width * 3),
                data = flipped.GetRawTextureData(),
            });
        }

        void PublishLabels(HeaderMsg header)
        {
            var flipped = FlipVertically(_labelTex);
            if (labelAsPng)
            {
                // PNG, never JPEG. JPEG bleeds colour across class boundaries
                // and no downstream code can undo it.
                _ros.Publish(labelTopic + "/compressed", new CompressedImageMsg
                {
                    header = header,
                    format = "png",
                    data = ImageConversion.EncodeToPNG(flipped),
                });
                return;
            }

            var raw = flipped.GetRawTextureData();
            _ros.Publish(labelTopic, new ImageMsg
            {
                header = header,
                height = (uint)height,
                width = (uint)width,
                encoding = "rgb8",
                is_bigendian = 0,
                step = (uint)(width * 3),
                data = raw,
            });
        }

        /// <summary>
        /// Unity textures start bottom-left, ROS images start top-left.
        ///
        /// Skipping this does NOT break IoU -- both images flip together, so the
        /// score is unaffected -- which is precisely why it is worth doing here
        /// rather than discovering it during the demo: the panel shows an
        /// upside-down Mars and X2 repaints an upside-down scene, and the
        /// numbers give no hint that anything is wrong.
        /// </summary>
        Texture2D FlipVertically(Texture2D src)
        {
            var pixels = src.GetPixels32();
            var flipped = new Color32[pixels.Length];
            for (int y = 0; y < src.height; y++)
            {
                System.Array.Copy(pixels, y * src.width,
                                  flipped, (src.height - 1 - y) * src.width,
                                  src.width);
            }
            src.SetPixels32(flipped);
            src.Apply(false);
            return src;
        }

        void OnDestroy()
        {
            if (_rgbRt != null) _rgbRt.Release();
            if (_labelRt != null) _labelRt.Release();
        }
    }
}

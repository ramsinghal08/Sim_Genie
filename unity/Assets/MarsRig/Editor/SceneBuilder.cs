using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace MarsRig.EditorTools
{
    /// <summary>
    /// Builds the scored scene from a menu item, deterministically.
    ///
    /// Mars Rig > Build Scored Scene        the scene that is measured
    /// Mars Rig > Build Calibration Scene   same terrain with the ArUco markers
    ///                                      and traffic cone, for a one-off
    ///                                      structure check
    ///
    /// Built from script so that the same seed gives the same terrain, rocks
    /// and path on every machine, keeping score changes attributable to the
    /// visual condition. The markers and cone must not appear in the scored
    /// scene; they live only in the calibration scene.
    /// </summary>
    public static class SceneBuilder
    {
        const int Seed = 20260822;
        const float TerrainRadius = 300f;   // extends past the far clip, no black
        const int TerrainGrid = 240;        // band at the horizon
        const int RockCount = 180;

        [MenuItem("Mars Rig/Build Scored Scene")]
        public static void BuildScored() { Build(false); }

        [MenuItem("Mars Rig/Build Calibration Scene")]
        public static void BuildCalibration() { Build(true); }

        static void Build(bool calibration)
        {
            var root = new GameObject(calibration ? "MarsRig_Calibration" : "MarsRig_Scored");
            var rng = new System.Random(Seed);

            BuildLighting(root);
            var terrain = BuildTerrain(root);
            BuildRocks(root, terrain, rng);
            if (calibration) BuildCalibrationProps(root, terrain);
            BuildRover(root, terrain);

            Selection.activeGameObject = root;
            Debug.Log($"[MarsRig] built {root.name}. " +
                      "Next: run Mars Rig > Check Camera Alignment before anything else.");
        }

        // ---------- lighting ----------

        static void BuildLighting(GameObject root)
        {
            var go = new GameObject("Sun");
            go.transform.SetParent(root.transform);
            var light = go.AddComponent<Light>();
            light.type = LightType.Directional;
            light.intensity = 1.1f;
            light.color = new Color(1f, 0.95f, 0.88f);
            // Moderately low sun: visible shadows without making the baseline hard.
            go.transform.rotation = Quaternion.Euler(35f, 140f, 0f);

            RenderSettings.ambientMode = AmbientMode.Flat;
            RenderSettings.ambientLight = new Color(0.32f, 0.28f, 0.26f);
            // No fog: it flattens distant contrast, which is a measured condition.
            // The terrain extends past the far clip instead.
            RenderSettings.fog = false;
        }

        // ---------- terrain ----------

        static GameObject BuildTerrain(GameObject root)
        {
            var go = new GameObject("Terrain");
            go.transform.SetParent(root.transform);

            var mesh = new Mesh { indexFormat = IndexFormat.UInt32, name = "MarsTerrain" };
            int n = TerrainGrid + 1;
            var verts = new Vector3[n * n];
            var uvs = new Vector2[n * n];
            var tris = new int[TerrainGrid * TerrainGrid * 6];

            for (int z = 0; z < n; z++)
            {
                for (int x = 0; x < n; x++)
                {
                    float fx = (x / (float)TerrainGrid) * 2f - 1f;
                    float fz = (z / (float)TerrainGrid) * 2f - 1f;
                    var world = new Vector2(fx * TerrainRadius, fz * TerrainRadius);
                    verts[z * n + x] = new Vector3(world.x, Height(world.x, world.y), world.y);
                    uvs[z * n + x] = new Vector2(x / (float)TerrainGrid, z / (float)TerrainGrid);
                }
            }

            int t = 0;
            for (int z = 0; z < TerrainGrid; z++)
            {
                for (int x = 0; x < TerrainGrid; x++)
                {
                    int i = z * n + x;
                    tris[t++] = i; tris[t++] = i + n; tris[t++] = i + 1;
                    tris[t++] = i + 1; tris[t++] = i + n; tris[t++] = i + n + 1;
                }
            }

            mesh.vertices = verts;
            mesh.uv = uvs;
            mesh.triangles = tris;
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();

            go.AddComponent<MeshFilter>().sharedMesh = mesh;
            var mr = go.AddComponent<MeshRenderer>();
            mr.sharedMaterial = MakeMaterial("MarsGround", new Color(0.55f, 0.34f, 0.24f), 0.95f);
            go.AddComponent<MeshCollider>().sharedMesh = mesh;

            // The ground is the drivable class; objects standing on it are Obstacle.
            go.AddComponent<SemanticLabel>().marsClass = MarsClass.Drivable;
            return go;
        }

        /// <summary>Terrain height: gentle dunes, one slope, one crater lip.</summary>
        public static float Height(float x, float z)
        {
            // Perlin noise is deterministic, so no heightmap needs storing.
            float dunes =
                Mathf.PerlinNoise(x * 0.010f + 100f, z * 0.010f + 100f) * 2.2f +
                Mathf.PerlinNoise(x * 0.045f + 300f, z * 0.045f + 300f) * 0.45f;

            // A slope the rover climbs, running across the path.
            float slope = Mathf.Clamp((z - 18f) / 40f, 0f, 1f) * 3.5f;

            // A crater lip: a raised ring around a depression.
            var toCrater = new Vector2(x - 26f, z - 34f).magnitude;
            float crater = 0f;
            if (toCrater < 18f)
            {
                float r = toCrater / 18f;
                // Raised rim near r=1, bowl in the middle.
                crater = Mathf.Sin(r * Mathf.PI) * 1.6f - Mathf.Cos(r * Mathf.PI * 0.5f) * 2.4f;
            }
            return dunes + slope + crater;
        }

        // ---------- rocks ----------

        static void BuildRocks(GameObject root, GameObject terrain, System.Random rng)
        {
            var parent = new GameObject("Rocks");
            parent.transform.SetParent(root.transform);
            var mat = MakeMaterial("MarsRock", new Color(0.34f, 0.27f, 0.23f), 0.8f);

            for (int i = 0; i < RockCount; i++)
            {
                // Clustered near the path so the camera sees enough obstacle pixels.
                float x = (float)(rng.NextDouble() * 70.0 - 35.0);
                float z = (float)(rng.NextDouble() * 90.0 - 15.0);
                float scale = (float)(0.25 + rng.NextDouble() * 1.5);

                var rock = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                rock.name = $"Rock_{i:D3}";
                rock.transform.SetParent(parent.transform);
                rock.transform.position = new Vector3(x, Height(x, z) + scale * 0.32f, z);
                rock.transform.rotation = Quaternion.Euler(
                    (float)(rng.NextDouble() * 360), (float)(rng.NextDouble() * 360),
                    (float)(rng.NextDouble() * 360));
                // Flattened, unevenly scaled so they read as rocks.
                rock.transform.localScale = new Vector3(
                    scale, scale * (float)(0.5 + rng.NextDouble() * 0.4),
                    scale * (float)(0.7 + rng.NextDouble() * 0.6));
                rock.GetComponent<MeshRenderer>().sharedMaterial = mat;
                rock.AddComponent<SemanticLabel>().marsClass = MarsClass.Obstacle;
            }
        }

        // ---------- calibration props ----------

        static void BuildCalibrationProps(GameObject root, GameObject terrain)
        {
            var parent = new GameObject("CalibrationProps");
            parent.transform.SetParent(root.transform);

            var cone = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            cone.name = "TrafficCone";
            cone.transform.SetParent(parent.transform);
            cone.transform.position = new Vector3(2f, Height(2f, 12f) + 0.4f, 12f);
            cone.transform.localScale = new Vector3(0.35f, 0.4f, 0.35f);
            cone.GetComponent<MeshRenderer>().sharedMaterial =
                MakeMaterial("Cone", new Color(0.95f, 0.35f, 0.05f), 0.7f);
            cone.AddComponent<SemanticLabel>().marsClass = MarsClass.Obstacle;

            for (int i = 0; i < 3; i++)
            {
                var marker = GameObject.CreatePrimitive(PrimitiveType.Quad);
                marker.name = $"ArUco_{i}";
                marker.transform.SetParent(parent.transform);
                float x = -3f + i * 3f;
                marker.transform.position = new Vector3(x, Height(x, 14f) + 0.8f, 14f);
                marker.transform.localScale = Vector3.one * 0.6f;
                marker.GetComponent<MeshRenderer>().sharedMaterial =
                    MakeMaterial($"Marker{i}", Color.white, 0.6f);
                marker.AddComponent<SemanticLabel>().marsClass = MarsClass.Obstacle;
            }

            Debug.LogWarning("[MarsRig] Calibration scene contains markers and a cone. " +
                             "Use it for ONE structure measurement. Never sweep against it.");
        }

        // ---------- rover and camera ----------

        static void BuildRover(GameObject root, GameObject terrain)
        {
            var rover = new GameObject("Rover");
            rover.transform.SetParent(root.transform);

            var body = GameObject.CreatePrimitive(PrimitiveType.Cube);
            body.name = "Body";
            body.transform.SetParent(rover.transform);
            body.transform.localPosition = new Vector3(0f, 0.35f, 0f);
            body.transform.localScale = new Vector3(0.8f, 0.4f, 1.1f);
            body.GetComponent<MeshRenderer>().sharedMaterial =
                MakeMaterial("RoverBody", new Color(0.7f, 0.7f, 0.72f), 0.4f);
            // The rover body is Obstacle if it appears in frame (normally it does not).
            body.AddComponent<SemanticLabel>().marsClass = MarsClass.Obstacle;

            var path = rover.AddComponent<RoverPath>();
            path.speed = 1.4f;
            path.loop = true;
            path.waypoints = new[]
            {
                new Vector3(0f, 0f, 0f),
                new Vector3(6f, 0f, 22f),
                new Vector3(-4f, 0f, 44f),
                new Vector3(10f, 0f, 62f),
                new Vector3(0f, 0f, 80f),
            };
            for (int i = 0; i < path.waypoints.Length; i++)
            {
                var w = path.waypoints[i];
                path.waypoints[i] = new Vector3(w.x, Height(w.x, w.z), w.z);
            }

            var camGo = new GameObject("Camera1_RGB_and_Labels");
            camGo.transform.SetParent(rover.transform);
            camGo.transform.localPosition = new Vector3(0f, 1.1f, 0.4f);
            // Tilted down so ground fills roughly 75% of frame. perception_node's
            // horizon_frac default of 0.25 assumes this -- retune it together.
            camGo.transform.localRotation = Quaternion.Euler(22f, 0f, 0f);

            var cam = camGo.AddComponent<Camera>();
            cam.fieldOfView = 60f;
            cam.nearClipPlane = 0.05f;
            // Beyond the terrain radius so terrain always reaches the horizon.
            cam.farClipPlane = 500f;
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(0.78f, 0.66f, 0.52f);
            // MSAA off: antialiased edges blend two class colours.
            // No post-processing on this camera; if a volume is added, exclude
            // motion blur, which makes RGB and label edges disagree.
            cam.allowMSAA = false;
            cam.tag = "MainCamera";

            camGo.AddComponent<DualCapture>();
        }

        // ---------- helpers ----------

        static Material MakeMaterial(string name, Color color, float smoothnessInverse)
        {
            var shader = Shader.Find("Standard");
            var m = new Material(shader) { name = name };
            m.color = color;
            if (m.HasProperty("_Glossiness")) m.SetFloat("_Glossiness", 1f - smoothnessInverse);
            if (m.HasProperty("_Metallic")) m.SetFloat("_Metallic", 0f);
            return m;
        }
    }
}

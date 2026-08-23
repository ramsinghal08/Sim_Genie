using System.Collections.Generic;
using RosMessageTypes.Geometry;
using RosMessageTypes.Std;
using Unity.Robotics.ROSTCPConnector.ROSGeometry;
using Unity.Robotics.ROSTCPConnector;
using UnityEngine;

namespace MarsRig
{
    /// <summary>
    /// Drives the rover along a fixed scripted path and publishes its pose.
    ///
    /// WHY THE POSE IS A PURE FUNCTION OF TIME
    /// ---------------------------------------
    /// BUILD_SPEC 5.1: the path must be identical every run, because identical
    /// motion is what makes a score change attributable to the visual condition
    /// alone. If two runs see different rocks at different distances, the IoU
    /// difference between them is partly geometry and there is no way to
    /// separate the two afterwards.
    ///
    /// Integrating velocity with Time.deltaTime does NOT give that. A frame
    /// hitch, a different machine, or the editor stealing a frame changes the
    /// accumulated distance, so the rover ends up somewhere slightly different
    /// each run and nothing reports a problem. Evaluating position directly
    /// from elapsed time removes the frame rate from the answer entirely.
    ///
    /// This is deliberately NOT teleop and deliberately does not subscribe to
    /// /cmd_vel. If the path is ever driven from ROS instead, it goes to Unity
    /// -- never to X2, which has no coordinate system (BUILD_SPEC 2).
    /// </summary>
    public class RoverPath : MonoBehaviour
    {
        [Header("Path")]
        [Tooltip("Waypoints in world space. The rover walks the polyline through them.")]
        public Vector3[] waypoints = new Vector3[0];

        public float speed = 1.4f;
        public bool loop = true;

        [Tooltip("Seconds to hold still at the start, so the bridge, the model and " +
                 "the scorer are all running before the scene begins to move.")]
        public float startDelay = 2f;

        [Header("Facing")]
        public bool faceAlongPath = true;
        [Tooltip("Degrees per second. Low values smooth the heading through a corner.")]
        public float turnRate = 120f;

        [Header("ROS")]
        public bool publishPose = true;
        public string poseTopic = "/unity/rover_pose";
        public string frameId = "map";
        public int poseFps = 10;

        ROSConnection _ros;
        float[] _cumulative;      // arc length at each waypoint
        float _total;
        float _startTime;
        float _nextPose;

        void Start()
        {
            _startTime = Time.time;
            BuildArcTable();
            if (publishPose)
            {
                _ros = ROSConnection.GetOrCreateInstance();
                _ros.RegisterPublisher<PoseStampedMsg>(poseTopic);
            }
        }

        void BuildArcTable()
        {
            if (waypoints == null || waypoints.Length < 2)
            {
                _cumulative = new float[0];
                _total = 0f;
                return;
            }
            var n = waypoints.Length + (loop ? 1 : 0);
            _cumulative = new float[n];
            _cumulative[0] = 0f;
            for (int i = 1; i < n; i++)
            {
                var a = waypoints[(i - 1) % waypoints.Length];
                var b = waypoints[i % waypoints.Length];
                _cumulative[i] = _cumulative[i - 1] + Vector3.Distance(a, b);
            }
            _total = _cumulative[n - 1];
        }

        void Update()
        {
            if (_total <= 0f) return;

            var elapsed = Mathf.Max(0f, Time.time - _startTime - startDelay);
            var distance = elapsed * speed;
            if (loop) distance = Mathf.Repeat(distance, _total);
            else distance = Mathf.Min(distance, _total);

            var position = PointAt(distance);
            transform.position = position;

            if (faceAlongPath)
            {
                // Look a little ahead rather than at the next waypoint, so the
                // heading turns smoothly through a corner instead of snapping.
                var ahead = PointAt(loop ? Mathf.Repeat(distance + 0.5f, _total)
                                         : Mathf.Min(distance + 0.5f, _total));
                var forward = ahead - position;
                if (forward.sqrMagnitude > 1e-6f)
                {
                    transform.rotation = Quaternion.RotateTowards(
                        transform.rotation,
                        Quaternion.LookRotation(forward.normalized, Vector3.up),
                        turnRate * Time.deltaTime);
                }
            }

            if (publishPose && Time.time >= _nextPose)
            {
                _nextPose = Time.time + 1f / Mathf.Max(1, poseFps);
                PublishPose();
            }
        }

        Vector3 PointAt(float distance)
        {
            for (int i = 1; i < _cumulative.Length; i++)
            {
                if (distance > _cumulative[i]) continue;
                var span = _cumulative[i] - _cumulative[i - 1];
                var t = span > 1e-6f ? (distance - _cumulative[i - 1]) / span : 0f;
                return Vector3.Lerp(waypoints[(i - 1) % waypoints.Length],
                                    waypoints[i % waypoints.Length], t);
            }
            return waypoints[waypoints.Length - 1];
        }

        void PublishPose()
        {
            _ros.Publish(poseTopic, new PoseStampedMsg
            {
                header = RosTime.Header(frameId),
                pose = new PoseMsg
                {
                    // Unity is left-handed Y-up, ROS is right-handed Z-up.
                    // To<FLU>() is the package's own conversion, with implicit
                    // operators to PointMsg and QuaternionMsg. Using it rather
                    // than a hand-written axis swap means the sign convention
                    // is theirs to get right, and it already is.
                    position = transform.position.To<FLU>(),
                    orientation = transform.rotation.To<FLU>(),
                },
            });
        }

        void OnDrawGizmosSelected()
        {
            if (waypoints == null || waypoints.Length < 2) return;
            Gizmos.color = Color.cyan;
            var n = waypoints.Length + (loop ? 1 : 0);
            for (int i = 1; i < n; i++)
            {
                Gizmos.DrawLine(waypoints[(i - 1) % waypoints.Length],
                                waypoints[i % waypoints.Length]);
            }
            foreach (var w in waypoints) Gizmos.DrawSphere(w, 0.2f);
        }
    }
}

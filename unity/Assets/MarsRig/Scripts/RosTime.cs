using RosMessageTypes.BuiltinInterfaces;
using RosMessageTypes.Std;

namespace MarsRig
{
    /// <summary>
    /// ROS 2 timestamps, from the wall clock.
    ///
    /// WHY NOT Unity.Robotics.Core.Clock
    /// ---------------------------------
    /// It does not exist in ROS-TCP-Connector. It ships with some of Unity's
    /// robotics *samples*, not with the package, so code that calls it compiles
    /// on the machine that has the sample imported and fails on everyone
    /// else's. Verified against the package source: `Unity.Robotics.Core` is
    /// not in it.
    ///
    /// WHY THE WALL CLOCK AND NOT Time.time
    /// ------------------------------------
    /// scoring_node pairs the label frame with the prediction using
    /// ApproximateTimeSynchronizer over these stamps, and every rclpy node
    /// stamps with system time. Unity's Time.time counts from editor play, so
    /// its stamps would sit decades away from the ROS side and the
    /// synchroniser would simply never fire a callback -- no error, no score,
    /// no clue why.
    ///
    /// REQUIRES ROS 2. builtin_interfaces/Time declares `int sec` under the
    /// ROS2 scripting define and `uint sec` without it, so set
    /// Robotics > ROS Settings > Protocol to ROS2 (which our whole stack needs
    /// regardless) or this will not compile.
    /// </summary>
    public static class RosTime
    {
        static readonly System.DateTime Epoch =
            new System.DateTime(1970, 1, 1, 0, 0, 0, System.DateTimeKind.Utc);

        public static TimeMsg Now()
        {
            // Ticks are 100 ns units, so this is exact. Going via a double of
            // total seconds loses precision at the nanosecond end and can round
            // the fraction up to a full second, producing a nanosec of 1e9 that
            // is outside the field's legal range.
            long ticks = (System.DateTime.UtcNow - Epoch).Ticks;
            return new TimeMsg
            {
                sec = (int)(ticks / System.TimeSpan.TicksPerSecond),
                nanosec = (uint)((ticks % System.TimeSpan.TicksPerSecond) * 100L),
            };
        }

        public static HeaderMsg Header(string frameId)
        {
            return new HeaderMsg { frame_id = frameId, stamp = Now() };
        }
    }
}

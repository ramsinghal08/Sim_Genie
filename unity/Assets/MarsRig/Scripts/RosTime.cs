using RosMessageTypes.BuiltinInterfaces;
using RosMessageTypes.Std;

namespace MarsRig
{
    /// <summary>
    /// ROS 2 timestamps from the wall clock.
    ///
    /// Unity.Robotics.Core.Clock is not part of ROS-TCP-Connector (it ships
    /// with Unity's robotics samples), so it is not used here.
    ///
    /// The wall clock is used instead of Time.time because scoring_node pairs
    /// frames with ApproximateTimeSynchronizer, and rclpy nodes stamp with
    /// system time. Time.time counts from editor play, so its stamps would
    /// never match and the synchronizer would never fire.
    ///
    /// Requires ROS 2: builtin_interfaces/Time declares `int sec` under the
    /// ROS2 scripting define and `uint sec` without it. Set
    /// Robotics > ROS Settings > Protocol to ROS2.
    /// </summary>
    public static class RosTime
    {
        static readonly System.DateTime Epoch =
            new System.DateTime(1970, 1, 1, 0, 0, 0, System.DateTimeKind.Utc);

        public static TimeMsg Now()
        {
            // Ticks are 100 ns units, so integer arithmetic is exact. A double
            // of total seconds can round nanosec up to 1e9, which is out of range.
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

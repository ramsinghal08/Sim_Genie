using System.Collections.Generic;
using UnityEngine;

namespace MarsRig
{
    /// <summary>
    /// Semantic classes rendered by the label camera and read by the scorer.
    /// Palette values must match DEFAULT_PALETTE in mars_rig/mars_rig/labels.py.
    /// A mismatch does not raise an error; the scorer maps pixels to the wrong
    /// class. Change both places, or pass `palette_json` to scoring_node.
    /// </summary>
    public enum MarsClass
    {
        Unlabelled = 0,
        Drivable = 1,
        Obstacle = 2,
        Sky = 3,
    }

    public static class MarsClasses
    {
        // Saturated and well separated so nearest-colour assignment survives
        // resizing and re-encoding.
        public static readonly Dictionary<MarsClass, Color32> Palette =
            new Dictionary<MarsClass, Color32>
        {
            { MarsClass.Unlabelled, new Color32(0, 0, 0, 255) },
            { MarsClass.Drivable,   new Color32(0, 255, 0, 255) },
            { MarsClass.Obstacle,   new Color32(255, 0, 0, 255) },
            { MarsClass.Sky,        new Color32(0, 0, 255, 255) },
        };

        public static Color32 ColorOf(MarsClass c)
        {
            Color32 value;
            return Palette.TryGetValue(c, out value) ? value : Palette[MarsClass.Unlabelled];
        }
    }
}

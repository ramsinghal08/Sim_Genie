using System.Collections.Generic;
using UnityEngine;

namespace MarsRig
{
    /// <summary>
    /// The semantic classes the label camera renders and the scorer reads.
    ///
    /// These values MUST match DEFAULT_PALETTE in mars_rig/mars_rig/labels.py.
    /// The scorer assigns each pixel to the nearest palette colour, so a
    /// mismatch here does not throw -- it quietly maps every rock to the wrong
    /// class and reports a plausible, meaningless IoU. If you change a colour,
    /// change it in both places, or pass the new palette to scoring_node as its
    /// `palette_json` parameter and leave the file alone.
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
        // Deliberately saturated and far apart in RGB: the scorer's
        // nearest-colour assignment has to survive a resize and a re-encode,
        // and colours that sit close together are what makes that fail.
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

using UnityEngine;

namespace MarsRig
{
    /// <summary>
    /// Tags a renderer with the class the label camera paints it.
    /// Objects without this component render as Unlabelled and are ignored by
    /// the metric, so a forgotten object cannot count as drivable ground.
    /// </summary>
    [DisallowMultipleComponent]
    public class SemanticLabel : MonoBehaviour
    {
        public MarsClass marsClass = MarsClass.Obstacle;

        [Tooltip("Apply to child renderers too. On for a rock made of several meshes.")]
        public bool includeChildren = true;

        public Renderer[] Renderers()
        {
            return includeChildren
                ? GetComponentsInChildren<Renderer>(false)
                : GetComponents<Renderer>();
        }
    }
}

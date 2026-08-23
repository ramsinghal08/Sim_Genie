using UnityEngine;

namespace MarsRig
{
    /// <summary>
    /// Tags a renderer with the class the label camera should paint it.
    ///
    /// Attach to anything that appears in the scored scene. Anything WITHOUT
    /// this component renders as Unlabelled and is ignored by the metric --
    /// which is the safe default: a forgotten object cannot silently count as
    /// drivable ground.
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

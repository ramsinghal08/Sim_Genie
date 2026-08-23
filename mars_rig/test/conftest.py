"""Make `import mars_rig` find the package, not the directory above it.

The colcon package root is `mars_rig/` and so is the python package inside it,
so running pytest from the repo root picks up the outer directory as a
namespace package and every import fails. Putting the package root first on
sys.path resolves it the same way an installed package would, so the tests run
identically from the repo root, from mars_rig/, and after a colcon build.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

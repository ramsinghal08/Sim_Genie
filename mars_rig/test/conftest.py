"""Put the package root first on sys.path so `import mars_rig` resolves to the
package, not the same-named outer directory (a namespace package)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

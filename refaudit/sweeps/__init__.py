"""The sweeps, in the order a full run takes them. Later sweeps can use earlier results."""
from .compare import Compare
from .consistency import Consistency
from .format import Format
from .lint import Lint
from .verify import Verify

REGISTRY = {s.name: s for s in (Compare(), Verify(), Format(), Lint(), Consistency())}

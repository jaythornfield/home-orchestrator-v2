"""Home Orchestrator application package."""

def __getattr__(name):
    if name == "HomeOrchestrator":
        from .orchestrator import HomeOrchestrator
        return HomeOrchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["HomeOrchestrator"]

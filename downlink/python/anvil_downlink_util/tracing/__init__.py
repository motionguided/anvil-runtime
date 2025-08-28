try:
    import opentelemetry.sdk
except ImportError:
    from anvil_downlink_util.tracing.dummy import *
else:
    from anvil_downlink_util.tracing.impl import *
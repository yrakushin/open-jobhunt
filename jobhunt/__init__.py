"""Open JobHunt — локальный ассистент откликов на job-сайтах."""

try:
    from importlib.metadata import version

    __version__ = version("open-jobhunt")
except Exception:
    __version__ = "0.2.5"

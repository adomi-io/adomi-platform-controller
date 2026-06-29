from . import controllers, models, wizards


def post_init_hook(env):
    """Seed the Authentik OIDC provider from the environment on install."""
    env["adomi.oidc.setup"].setup_from_env()

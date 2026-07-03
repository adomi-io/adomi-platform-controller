"""The Adomi platform API — the front door to the platform.

End users drive the platform through this API (directly, or via the Odoo portal, the
CLI, or partner UIs). It speaks the **same object language as the operator** — one API
per controller object (Client, Environment, Application, Database, Domain, GitRepository,
Snapshot). Every non-ephemeral resource a user creates is turned into a
``platform.adomi.io`` custom resource and **committed to that client's client git
repo**, which Argo CD reconciles and the adomi-platform-controller turns into running
infrastructure; reads return live status from the cluster. Ephemeral resources (PR
preview environments) are the deliberate exception and are created in-cluster by the
controller, not through this API.

The resource model is defined once in :mod:`adomi_platform_schema` and shared with
the controller so the two halves of the control plane never drift.
"""

__version__ = "0.1.0"

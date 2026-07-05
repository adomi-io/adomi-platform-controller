"""Static lint of the OWL templates: catch what only compiles in a browser.

OWL compiles templates client-side, so a bad expression ships green through the
whole server-side suite and explodes on first render (it took down the whole
Observability tab once). Guard the known trap here.
"""

import pathlib
import re
import xml.etree.ElementTree as ET

from odoo.tests.common import TransactionCase

COMPONENTS = pathlib.Path(__file__).resolve().parent.parent / "static" / "src" / "components"

# OWL's expression tokenizer splits `1e9` into `1` + variable `e9`, generating
# invalid JavaScript ("identifier starts immediately after numeric literal") —
# the WHOLE template fails to compile. `#017e84` (hex colors) are fine: the
# lookbehind skips digits/word chars/# immediately before.
EXPONENT_LITERAL = re.compile(r"(?<![\w#])\d+(?:\.\d+)?e\d+\b")


class TestTemplateLint(TransactionCase):
    def test_no_exponent_literals_in_owl_expressions(self):
        offenders = []
        for xml_file in sorted(COMPONENTS.glob("**/*.xml")):
            for node in ET.parse(xml_file).iter():
                for attr, value in node.attrib.items():
                    if attr.startswith("t-") and EXPONENT_LITERAL.search(value):
                        offenders.append("%s: %s=%r" % (xml_file.name, attr, value))
        self.assertFalse(
            offenders,
            "Exponent literals (1e9, …) break OWL template compilation — "
            "move the math into the component:\n%s" % "\n".join(offenders),
        )

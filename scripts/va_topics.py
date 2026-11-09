"""Virtual Agent Designer topics for the demo, built as sys_cb_topic records (the Designer's own format).

The graphs follow the node/edge format of the out-of-box topics on the instance (Prompt, ChoicePrompt,
BooleanPrompt, ScriptAction, TextOutput). ServiceNow compiles a topic into its runtime record only when it is
published, so after setup open each topic in Virtual Agent Designer and click Publish once.

The topics only collect answers; the Script Include AWXDemoAgent creates the ticket, which fires the
"AWX Demo - launch" Business Rule exactly like the web form does.
"""
import json
import uuid

GRAPH_API_VERSION = "1.0.41"
_NS = uuid.UUID("6f1c3c55-6a2e-4d36-9a57-3a3f1d1d0a11")


def _id(*parts):
    """Stable ids, so re-running setup updates the same nodes instead of creating new ones."""
    return uuid.uuid5(_NS, "/".join(parts)).hex


def _rich(text):
    return {"value": [{"type": "paragraph", "children": [{"text": text}]}], "mode": "simple"}


def _simple(value=""):
    return {"value": value, "mode": "simple"}


def _query(value=""):
    return {"value": value, "mode": "query"}


def _script_applicability(js):
    return {"value": f"(function execute() {{\n    {js}\n}})()\n", "mode": "script"}


class _Topic:
    def __init__(self, key):
        self.key, self.nodes, self.edges, self.order = key, {}, [], []
        self.variables = {"current-user": {"category": "vaInputs", "id": "current-user", "label": "User",
                                           "name": "user", "table": "sys_user", "varType": "reference"}}
        self.add({"stepType": "Start", "name": "Start", "greetingResponse": _simple()}, "start")

    def add(self, node, key):
        node["id"] = _id(self.key, "node", key)
        self.nodes[node["id"]] = node
        self.order.append(node["id"])
        return node["id"]

    def _input_var(self, key, name, label, var_type, node_id, **extra):
        vid = _id(self.key, "var", key)
        self.variables[vid] = {"id": vid, "name": name, "label": label, "varType": var_type, "category": "vaInputs",
                               "nodeId": node_id, "isHidden": False, **extra}
        return vid

    def _prompt_common(self, text):
        return {"prompt": _rich(text), "confirmMessage": _simple(), "acknowledgeMsg": _simple(),
                "applicability": _query(), "skippable": _query(), "defaultValue": _simple(),
                "withNluUserInput": False, "skipIfFulfilled": False}

    def text_input(self, name, label, text):
        node = {"stepType": "Prompt", "name": label, **self._prompt_common(text),
                "inputType": {"format": "Text", "script": ""}}
        nid = self.add(node, name)
        node["variableId"] = self._input_var(name, name, label, "string", nid, secure=False)

    def choice_input(self, name, label, text, choices):
        node = {"stepType": "ChoicePrompt", "name": label, **self._prompt_common(text), "hasCardHeader": False,
                "cardMetadata": {"mode": "card", "metadataScript": "", "cardTemplate": "", "cardFields": {},
                                 "cardDefinition": {}}}
        nid = self.add(node, name)
        node["variableId"] = self._input_var(name, name, label, "choice", nid,
                                             choices=[{"label": lab, "value": val} for lab, val in choices])

    def boolean_input(self, name, label, text_script):
        node = {"stepType": "BooleanPrompt", "name": label, **self._prompt_common(""), "optional": False,
                "entity": "", "inputType": {"format": "Text", "script": ""}, "skipReprompt": _query(),
                "detailDescription": "", "hasCardHeader": False,
                "cardMetadata": {"mode": "card", "metadataScript": "", "cardTemplate": "", "cardFields": {},
                                 "cardDefinition": {}}}
        node["prompt"] = {"value": text_script, "mode": "script"}
        nid = self.add(node, name)
        node["variableId"] = self._input_var(name, name, label, "boolean", nid)

    def script_var(self, name):
        vid = _id(self.key, "var", name)
        self.variables[vid] = {"id": vid, "name": name, "label": name, "varType": "script", "category": "vaVars",
                               "value": {"mode": "script", "value": "(function execute() {\n    return '';\n})()\n"}}

    def script(self, key, label, js, applicability=None):
        self.add({"stepType": "ScriptAction", "name": label, "script": js,
                  "applicability": applicability or _query()}, key)

    def say(self, key, label, js, applicability=None):
        self.add({"stepType": "TextOutput", "name": label, "value": [{"value": js, "mode": "script"}],
                  "applicability": applicability or _query(), "secure": False, "requestFeedback": False}, key)

    def graph(self):
        self.add({"stepType": "TerminateGoal", "name": "End", "confirmationMessage": _simple()}, "end")
        edges = [{"id": _id(self.key, "edge", str(i)), "edgeType": "Edge", "source": a, "target": b,
                  "sourcePort": "", "targetPort": ""} for i, (a, b) in enumerate(zip(self.order, self.order[1:]))]
        return {"graph_api_version": GRAPH_API_VERSION,
                "goals": {"primary": {"id": "primary", "trigger": {"type": "primary"}, "name": "primary",
                                      "applicability": "", "description": "", "nodes": self.nodes, "edges": edges}},
                "variables": self.variables}


REPLY = "(function execute() {\n    return vaVars.reply;\n})()\n"


def _diagnose():
    t = _Topic("diagnose")
    t.script_var("reply")
    t.text_input("interface_name", "Interface", "Which interface is having problems? For example GigabitEthernet1")
    t.script("create", "Create incident and start AWX diagnostics",
             "(function execute() {\n"
             "    var r = new global.AWXDemoAgent().createDiagnosticsIncident('demo-iosxe', vaInputs.interface_name.getValue());\n"
             "    vaVars.reply = r.message + (r.link ? '\\nOpen the incident: ' + r.link : '');\n"
             "})()\n")
    t.say("reply", "Tell the user", REPLY)
    return t


def _change():
    confirmed = "var v = String(vaInputs.confirm.getValue()).toLowerCase();\n    return v == 'true' || v == '1' || v == 'yes';"
    t = _Topic("change")
    t.script_var("reply")
    t.text_input("loopback", "Loopback",
                 "Which loopback interface? For example Loopback200, or Loopback200-209 for up to 20 at once")
    t.choice_input("action", "Action", "What should I do with it?", [("Enable it", "enable"), ("Shut it down", "shutdown")])
    t.text_input("int_desc", "Description", "Which description should the interface get?")
    t.boolean_input("confirm", "Confirm",
                    "(function execute() {\n"
                    "    var what = vaInputs.action.getValue() == 'shutdown' ? 'shut down' : 'enable';\n"
                    "    return 'I will raise a change to ' + what + ' ' + vaInputs.loopback.getValue() + "
                    "' on demo-iosxe, with pre- and post-checks. Go ahead?';\n"
                    "})()\n")
    t.script("create", "Create change and start AWX",
             "(function execute() {\n"
             "    var r = new global.AWXDemoAgent().createInterfaceChange('demo-iosxe', vaInputs.loopback.getValue(),\n"
             "        vaInputs.int_desc.getValue(), vaInputs.action.getValue() == 'shutdown');\n"
             "    vaVars.reply = r.message + (r.link ? '\\nOpen the change: ' + r.link : '');\n"
             "})()\n", _script_applicability(confirmed))
    t.say("reply", "Tell the user", REPLY, _script_applicability(confirmed))
    t.say("cancelled", "Cancelled", "(function execute() {\n    return 'OK, nothing was changed.';\n})()\n",
          _script_applicability(confirmed.replace("return v ==", "return !(v ==").replace("'yes';", "'yes');")))
    return t


def _status():
    t = _Topic("status")
    t.script_var("reply")
    t.text_input("ticket", "Ticket number", "Which ticket? For example INC0010026 or CHG0030010")
    t.script("lookup", "Read automation status",
             "(function execute() {\n    vaVars.reply = new global.AWXDemoAgent().getStatus(vaInputs.ticket.getValue()).message;\n})()\n")
    t.say("reply", "Tell the user", REPLY)
    return t


TOPICS = [
    ("AWX Demo - Diagnose interface", "Diagnose a network interface",
     "awx diagnose,diagnose interface,interface down,network issue,diagnose network", _diagnose),
    ("AWX Demo - Change loopback", "Change a loopback interface",
     "awx loopback,loopback,enable interface,shut down interface,change loopback", _change),
    ("AWX Demo - Automation status", "Check automation status",
     "awx status,check status,automation status,ticket status", _status),
    # NLU runs before keyword matching on this instance, so each topic also has an "awx ..." keyword that no NLU
    # intent claims - type that in the chat to start the topic reliably.
]


def records():
    """(name, payload) for each sys_cb_topic record."""
    out = []
    for name, title, phrases, build in TOPICS:
        out.append((name, {
            "name": name, "title": title, "type": "STANDARD", "model_type": "nlu_keyword", "key_phrases": phrases,
            "roles": "itil", "applicability_mode": "query", "applicability": "",
            "description": f"{title} - creates a ticket that launches AWX (ServiceNow + AWX demo).",
            "graph": json.dumps(build().graph(), indent=2),
        }))
    return out

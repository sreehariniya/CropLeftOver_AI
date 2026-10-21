"""Run app.py against a fake `streamlit` to catch NameErrors, bad calls and broken flows.
It cannot check how the page looks.
Run from the project folder: python tests/smoke_ui.py"""
import os, sys, runpy, tempfile, types, contextlib
os.environ["CROPLEFTOVER_DB"] = os.path.join(tempfile.mkdtemp(), "smoke.db")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

class Rerun(Exception): pass
class Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False

class Fake(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state, self.query_params = {}, {}
        self.inputs, self.pressed, self.out = {}, set(), []
    # ---- output capture
    def _say(self, *a, **k): self.out.append(" ".join(str(x) for x in a if isinstance(x, str)))
    def __getattr__(self, name):
        if name in ("markdown","caption","write","info","success","warning","error","subheader","title","text","progress"):
            return self._say
        raise AttributeError(name)
    def set_page_config(self, **k): pass
    def cache_resource(self, f=None, **k): return f
    def container(self, **k): return Ctx()
    def spinner(self, *a, **k): return Ctx()
    def form(self, key, **k): return Ctx()
    def divider(self): pass
    def rerun(self): raise Rerun()
    def columns(self, n):
        return [self for _ in range(n)]
    # ---- widgets
    def _val(self, label, default, key=None):
        v = self.inputs.get(label, default)
        if key: v = self.session_state.setdefault(key, v)
        return v
    def selectbox(self, label, options, index=0, key=None, format_func=str, **k):
        options = list(options)
        for o in options: format_func(o)
        v = self.inputs.get(label, options[index] if options else None)
        if key:
            if key not in self.session_state: self.session_state[key] = v
            v = self.session_state[key]
            assert v in options, f"{key}={v!r} not in options"
        return v
    def radio(self, label, options, key=None, format_func=str, **k):
        options = list(options)
        for o in options: format_func(o)
        if key:
            self.session_state.setdefault(key, options[0])
            v = self.session_state[key]
            assert v in options, f"radio {key}={v!r} not in {options}"
            return v
        return options[0]
    def multiselect(self, label, options, default=None, format_func=str, **k):
        options = list(options)
        for o in options: format_func(o)
        for d in (default or []): assert d in options, f"default {d} not in options"
        return self.inputs.get(label, list(default or []))
    def text_input(self, label, value="", **k): return self.inputs.get(label, value)
    def number_input(self, label, value=0.0, **k): return self.inputs.get(label, value)
    def checkbox(self, label, value=False, **k): return self.inputs.get(label, value)
    def form_submit_button(self, label, **k): return label in self.pressed
    def button(self, label, key=None, on_click=None, args=(), **k):
        self.out.append("[button:" + label + "]")
        if key in self.pressed or label in self.pressed:
            if on_click: on_click(*args)
            self.pressed.discard(key); self.pressed.discard(label)
            return True
        return False

st = Fake()
sys.modules["streamlit"] = st

def run(inputs=None, pressed=()):
    """One script run, following st.rerun() until the page settles."""
    st.inputs, st.pressed = inputs or {}, set(pressed)
    for _ in range(5):
        st.out.clear()
        try:
            runpy.run_path(os.path.join(ROOT, "app.py"), run_name="__main__")
            return " | ".join(st.out)
        except Rerun:
            st.inputs, st.pressed = {}, set()
    raise RuntimeError("rerun loop")

def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: sys.exit(1)

# 1. new visitor sees registration and sign-in
page = run()
ok("Register once" in page and "Already registered" in page, "farmer sees register + sign-in")
# 2. validation
page = run(pressed=["Register"])
ok("Enter your name." in page, "empty registration shows errors")
# 3. register as farmer
page = run({"Your name": "Ravi", "Mobile number": "98765 43210", "District / location": "Thanjavur",
            "Allow sharing my phone number with a buyer after I accept to connect": True}, ["Register"])
ok("Tell us about your residue" in page and "Ravi" in page, "farmer registered and lands on Add page")
ok(st.query_params.get("f"), "sign-in token kept in URL")
# 4. duplicate phone is refused
saved = dict(st.session_state); st.session_state.clear(); st.query_params.clear()
page = run({"Your name": "Other", "Mobile number": "9876543210", "District / location": "X"}, ["Register"])
ok("already registered" in page, "duplicate mobile number refused")
# 5. sign in with number, and re-open via URL token
page = run({"Mobile number": "9876543210"}, ["Sign in"])
ok("Tell us about your residue" in page, "sign in with mobile number works")
token = st.query_params["f"]; st.session_state.clear()
st.query_params["f"] = token
page = run()
ok("Tell us about your residue" in page, "refresh keeps farmer signed in (token in URL)")
# 6. sample data before first listing
st.session_state["farmer_page_probe"] = 1
st.session_state["page_farmer"] = "results"
page = run()
ok("SAMPLE" in page and "Possible uses" in page and "Possible buyers" in page, "results show sample before first listing")
st.session_state["page_farmer"] = "requests"
page = run()
ok(page.count("SAMPLE") >= 3 and "Accepted" in page and "Pending" in page and "Declined" in page, "requests show 3 sample requests")
# 7. add a real listing
st.session_state["page_farmer"] = "add"
page = run({"Residue type": "rice_straw", "Quantity (kg)": 500.0, "District / location": "Thanjavur", "Expected price per kg (₹, optional)": 2.0}, ["Find uses and buyers"])
ok("Listing saved" in page and "SAMPLE" not in page and "Possible buyers" in page, "listing saved, real results replace sample")
# 8. buyer registers (separate session)
farmer_state = dict(st.session_state); farmer_params = dict(st.query_params)
st.session_state.clear(); st.query_params.clear()
st.session_state["role"] = "buyer"
page = run()
ok("Register your business" in page, "buyer sees business registration")
page = run({"Business name": "Thanjavur Mushroom Co", "Mobile number": "9123456780", "District / location": "Thanjavur",
            "Residues you buy": ["rice_straw"], "Conditions you accept": ["dry"]}, ["Save business"])
ok("Requests from farmers" in page and "Business details saved" in page, "buyer registered, lands on Requests")
ok("No requests yet" in page, "new buyer starts with empty inbox")
st.session_state["page_buyer"] = "b_listings"
page = run()
ok("Ravi" in page and "500 kg" in page, "buyer sees the farmer's matching listing")
buyer_state = dict(st.session_state); buyer_params = dict(st.query_params)
# 9. farmer requests the registered buyer
st.session_state.clear(); st.query_params.clear()
st.session_state.update(farmer_state); st.query_params.update(farmer_params)
st.session_state["page_farmer"] = "results"
page = run()
ok("Thanjavur Mushroom Co" in page and "REGISTERED" in page, "farmer sees registered buyer in results")
import db
bid = next(b["id"] for b in db.get_buyers_for("rice_straw") if not b["is_demo"])
lid = st.session_state["current_listing"]
page = run(pressed=[f"req_{lid}_{bid}"])
page = run()
ok("Request sent" in page, "connection request sent")
# 10. buyer accepts
st.session_state.clear(); st.query_params.clear()
st.session_state.update(buyer_state); st.query_params.update(buyer_params)
st.session_state["page_buyer"] = "b_requests"
page = run()
ok("Pending" in page and "Accept" in page, "buyer sees pending request with buttons")
rid = db.requests_for_buyer_user(db.user_by_token(buyer_params["b"])["id"])[0]["id"]
page = run(pressed=[f"acc_{rid}"])
page = run()
ok("Accepted" in page and "9876543210" in page, "after accepting, buyer sees the farmer's phone (farmer consented)")
# 11. farmer sees acceptance and buyer's number
st.session_state.clear(); st.query_params.clear()
st.session_state.update(farmer_state); st.query_params.update(farmer_params)
st.session_state["page_farmer"] = "requests"
page = run()
ok("Accepted" in page and "9123456780" in page, "farmer sees accepted status and buyer's number")
# 12. every language renders every page without error
for lang in ("ta", "te", "hi"):
    for role, pages in (("farmer", ["add", "results", "requests", "profile"]), ("buyer", ["b_requests", "b_listings", "b_profile"])):
        st.session_state.clear(); st.query_params.clear()
        src = farmer_state if role == "farmer" else buyer_state
        par = farmer_params if role == "farmer" else buyer_params
        st.session_state.update(src); st.query_params.update(par)
        st.session_state["lang"] = lang
        for p in pages:
            st.session_state[f"page_{role}"] = p
            run()
print("PASS all pages render in ta/te/hi for both roles")
# 13. sign out
st.session_state.clear(); st.query_params.clear()
st.session_state.update(farmer_state); st.query_params.update(farmer_params)
st.session_state["page_farmer"] = "profile"
run(pressed=["signout_farmer"])
page = run()
ok("Register once" in page and "f" not in st.query_params, "sign out returns to registration")
print("ALL SMOKE TESTS PASSED")

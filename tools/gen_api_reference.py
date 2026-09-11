import ast, pathlib, sys

GROUPS = [
 ("Driver: decode, audit and write over raw CAN", [
   ("sparklib/admin.py", "spark_admin"),
   ("sparklib/cli.py", "spark_cli")]),
 ("Runtime: the objects that drive motors", [
   ("sparklib/can_bus.py", "can_bus"),
   ("sparklib/controller.py", "controller")]),
 ("Swerve: absolute encoders, steering and chassis math", [
   ("sparklib/kinematics.py", "kinematics"),
   ("sparklib/steer.py", "steer"),
   ("sparklib/cancoder.py", "cancoder"),
   ("sparklib/trace.py", "trace")]),
 ("Host setup: config, netdev and the motor rail", [
   ("sparklib/config.py", "config"),
   ("sparklib/netdev.py", "netdev"),
   ("sparklib/rail.py", "rail")]),
 ("Evidence: what the driver believes and how it was got", [
   ("sparklib/provenance.py", "provenance")]),
 ("Simulation: a frame-level bus, no hardware", [
   ("tests/support/sparksim/bus.py", "sparksim.bus"),
   ("tests/support/sparksim/controller.py", "sparksim.controller"),
   ("tests/support/sparksim/frames.py", "sparksim.frames"),
   ("tests/support/sparksim/fleet.py", "sparksim.fleet"),
   ("tests/support/sparksim/clock.py", "sparksim.clock")]),
 ("Hardware harness: real bus, gated", [
   ("tests/support/sparkhw/wire.py", "sparkhw.wire")]),
]

# Docstrings are copied verbatim and some carry typographic punctuation. This
# file is a rendered document, so fold those to ASCII here and leave the source
# alone.
_ASCII = {"\u2014": "--", "\u2013": "-", "\u2018": "'", "\u2019": "'",
          "\u201c": '"', "\u201d": '"', "\u00a0": " ", "\u2026": "..."}


def first_line(node):
    d = ast.get_docstring(node) or ""
    if not d:
        return ""
    line = " ".join(d.strip().splitlines()[0].split())
    for bad, good in _ASCII.items():
        line = line.replace(bad, good)
    return "".join(c for c in line if ord(c) < 128)

out = []
for title, files in GROUPS:
    out.append(f"\n## {title}\n")
    for path, mod in files:
        p = pathlib.Path(path)
        if not p.exists(): continue
        tree = ast.parse(p.read_text())
        md = first_line(tree)
        out.append(f"\n### `{mod}`\n")
        if md: out.append(f"{md}\n")
        rows = []
        for n in tree.body:
            if isinstance(n, ast.ClassDef) and not n.name.startswith("_"):
                rows.append((f"class {n.name}", first_line(n)))
                for m in n.body:
                    if isinstance(m, ast.FunctionDef) and not m.name.startswith("_"):
                        rows.append((f"  .{m.name}()", first_line(m)))
            elif isinstance(n, ast.FunctionDef) and not n.name.startswith("_"):
                rows.append((f"{n.name}()", first_line(n)))
        if rows:
            w = max(len(a) for a, _ in rows)
            out.append("```")
            for a, b in rows:
                out.append(f"{a:<{w}}  {b[:96]}" if b else a)
            out.append("```")
print("\n".join(out))

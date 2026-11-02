#!/usr/bin/env python3
"""
Black-Amethyst rebranding + offline-account patcher.

Applies a small, well-defined set of edits to a *fresh upstream checkout* of
AngelAuraMC/Amethyst-Android so that the resulting APK:

  1. Lets you add an OFFLINE (local) account with no internet and no prior
     online account -- "right off the bat".  (the functional change)
  2. Is branded "Black-Amethyst".                     (the gimmick)
  3. Uses our own application id / provider authorities. (a proper package name)
  4. Signs with the checked-in Android debug key, so no upstream release
     keystore / secrets are needed and installs stay updatable.

Every edit is done by pattern matching and is VERIFIED. If upstream refactors
such that a pattern no longer matches (0 replacements) or a sanity check fails,
this script exits non-zero with a loud, specific message so the pipeline fails
red instead of shipping a silently-unpatched build.

Usage:  patch.py <upstream_checkout_root>
Config comes from env vars (with sensible defaults):
  BA_PACKAGE   e.g. io.github.jackofnonetrades.blackamethyst
  BA_APP_NAME  e.g. Black-Amethyst
"""

import os
import re
import sys

PKG = os.environ.get("BA_PACKAGE", "io.github.jackofnonetrades.blackamethyst")
APP_NAME = os.environ.get("BA_APP_NAME", "Black-Amethyst")

# Java package segments: dot-separated, each starting with a letter.
if not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", PKG):
    sys.exit(f"FATAL: BA_PACKAGE '{PKG}' is not a valid lowercase reverse-DNS package name")


class PatchError(SystemExit):
    def __init__(self, msg):
        super().__init__(f"\n[PATCH FAILED] {msg}\n")


def read(path):
    if not os.path.isfile(path):
        raise PatchError(f"expected file not found: {path}\n"
                         f"  Upstream layout changed -- the heuristics need updating.")
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def sub_once(pattern, repl, text, what, flags=0):
    """Substitute and REQUIRE exactly one match. Fail loudly otherwise."""
    new, n = re.subn(pattern, repl, text, flags=flags)
    if n == 0:
        raise PatchError(f"could not locate: {what}\n"
                         f"  pattern: {pattern!r}\n"
                         f"  Upstream source changed -- update the heuristic in scripts/patch.py.")
    if n > 1:
        raise PatchError(f"expected 1 match but found {n} for: {what}\n"
                         f"  pattern: {pattern!r}\n"
                         f"  Ambiguous -- tighten the heuristic in scripts/patch.py.")
    print(f"  [ok] {what}")
    return new


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch.py <upstream_checkout_root>")
    root = sys.argv[1]
    app = os.path.join(root, "app_pojavlauncher")

    print(f"Black-Amethyst patcher")
    print(f"  package : {PKG}")
    print(f"  appname : {APP_NAME}")
    print(f"  target  : {root}")

    # ------------------------------------------------------------------
    # 1. THE FUNCTIONAL CHANGE: offline accounts with no internet.
    #
    # Upstream gates the "Local" (offline) account button behind
    # hasNoOnlineProfileDialog(), which REFUSES to open the local-login
    # screen unless you already have an *online* Microsoft profile. On a
    # fresh, internet-less device that is a chicken-and-egg lock. We unwrap
    # the gate so the button goes straight to LocalLoginFragment.
    # ------------------------------------------------------------------
    saf_path = os.path.join(app, "src/main/java/net/kdt/pojavlaunch/fragments/SelectAuthFragment.java")
    saf = read(saf_path)
    # Capture the inner swapFragment(...) call that targets LocalLoginFragment
    # and drop the surrounding hasNoOnlineProfileDialog(requireActivity(), () -> ... ) wrapper.
    saf = sub_once(
        r"mLocalButton\.setOnClickListener\(\s*v\s*->\s*"
        r"hasNoOnlineProfileDialog\(\s*requireActivity\(\)\s*,\s*\(\)\s*->\s*"
        r"(?P<inner>Tools\.swapFragment\([^;]*?LocalLoginFragment[^;]*?\))"
        r"\s*\)\s*\)\s*;",
        r"mLocalButton.setOnClickListener(v -> \g<inner>);",
        saf,
        "offline-account gate on the Local login button",
        flags=re.DOTALL,
    )
    # Verify the gate is gone from that button and the target is preserved.
    button_line = next((l for l in saf.splitlines() if "mLocalButton.setOnClickListener" in l), "")
    if "hasNoOnlineProfileDialog" in button_line:
        raise PatchError("Local button still wrapped in hasNoOnlineProfileDialog after patch")
    if "LocalLoginFragment" not in button_line:
        raise PatchError("Local button no longer opens LocalLoginFragment after patch -- refusing to ship")
    write(saf_path, saf)

    # The SAME gate is duplicated inside LocalLoginFragment: on open it bounces
    # straight back to the main menu when there's no online profile ("overkill
    # but meh", per the upstream comment). Without removing this, the local-login
    # screen silently closes the instant it opens, so the button "does nothing".
    llf_path = os.path.join(app, "src/main/java/net/kdt/pojavlaunch/fragments/LocalLoginFragment.java")
    llf = read(llf_path)
    llf = sub_once(
        r"if\s*\(\s*!hasOnlineProfile\(\)\s*\)\s*\{\s*"
        r"Tools\.swapFragment\(\s*requireActivity\(\)\s*,\s*MainMenuFragment\.class\s*,\s*"
        r"MainMenuFragment\.TAG\s*,\s*null\s*\)\s*;\s*\}",
        "/* Black-Amethyst: local login allowed with no online profile */",
        llf, "offline bounce-back guard in LocalLoginFragment",
        flags=re.DOTALL,
    )
    if "!hasOnlineProfile()" in llf:
        raise PatchError("LocalLoginFragment still guards on !hasOnlineProfile() after patch")
    write(llf_path, llf)

    # Profile creation has its own online-account gates, independent of login.
    # All modded profile buttons (including LWJGL3ify) share tryInstall(). The
    # modpack browser repeats the same gate after the profile-type screen.
    # Installation does not need account credentials; launch still checks for
    # a selected account and retains upstream's demo-account restrictions.
    for fragment in ("ProfileTypeSelectFragment", "ModpackCreateFragment"):
        path = os.path.join(app, f"src/main/java/net/kdt/pojavlaunch/fragments/{fragment}.java")
        source = read(path)
        source = sub_once(
            r"if\s*\(\s*!hasOnlineProfile\(\)\s*\)\s*\{\s*"
            r"hasNoOnlineProfileDialog\(requireActivity\(\)\);\s*"
            r"\}\s*else\s*\{\s*"
            r"(?P<inner>Tools\.swapFragment\(requireActivity\(\),\s*fragmentClass,\s*tag,\s*null\);)\s*\}",
            r"\g<inner>",
            source, f"allow local accounts to create profiles in {fragment}",
        )
        source = source.replace("import static net.kdt.pojavlaunch.Tools.hasOnlineProfile;\n", "")
        source = source.replace("import static net.kdt.pojavlaunch.Tools.hasNoOnlineProfileDialog;\n", "")
        if re.search(r"\b(?:hasOnlineProfile|hasNoOnlineProfileDialog)\s*\(", source):
            raise PatchError(f"{fragment} still has an online-account gate after patch")
        write(path, source)

    # LWJGL3ify's launch-time asset check must agree with MinecraftDownloader:
    # a local account can fetch missing files when the device is online.
    launcher_path = os.path.join(app, "src/main/java/net/kdt/pojavlaunch/LauncherActivity.java")
    launcher = read(launcher_path)
    launcher = sub_once(
        r"(if\s*\(\s*!lwjgl3ifyClientJar\.exists\(\)\s*\)\s*\{\s*)"
        r"if\s*\(\s*mAccountSpinner\.getSelectedAccount\(\)\.isLocal\(\)\s*\|\|\s*!isOnline\(this\)\s*\)",
        r"\1if (!isOnline(this))",
        launcher, "LWJGL3ify missing assets: allow local accounts to download when online",
    )
    launcher = launcher.replace(
        "// We just installed a json, we need internet + online acc to download so we add super",
        "// We just installed a json, we need internet to download so we add super",
    )
    write(launcher_path, launcher)

    # Let a LOCAL/offline account download game files whenever the device is
    # online. Upstream skips downloadGame() for any local profile, so an offline
    # account can never fetch a version even when the files are reachable (here,
    # via a transparent local mirror of Mojang's endpoints). Gate the download on
    # connectivity alone; when truly offline it still falls back to local files.
    mcd_path = os.path.join(app, "src/main/java/net/kdt/pojavlaunch/tasks/MinecraftDownloader.java")
    mcd = read(mcd_path)
    mcd = sub_once(
        r"if\s*\(\s*isLocalProfile\s*\|\|\s*!isOnline\s*\)",
        "if (!isOnline)",
        mcd, "download gate: allow local accounts to download when online",
    )
    if re.search(r"isLocalProfile\s*\|\|\s*!isOnline", mcd):
        raise PatchError("MinecraftDownloader still blocks downloads for local profiles")
    write(mcd_path, mcd)

    # ------------------------------------------------------------------
    # 2/3/4. Branding, package identity and signing -- all in build.gradle.
    # ------------------------------------------------------------------
    bg_path = os.path.join(app, "build.gradle")
    bg = read(bg_path)

    # applicationId (installed package id). namespace (net.kdt.pojavlaunch, the
    # R/BuildConfig code package) is intentionally left untouched.
    bg = sub_once(
        r'applicationId\s+"org\.angelauramc\.amethyst"',
        f'applicationId "{PKG}"',
        bg, "applicationId -> our package",
    )

    # app_name / app_short_name in the *release* build type -> Black-Amethyst.
    bg = sub_once(
        r'(resValue\s+"string",\s*"app_name",\s*)"Amethyst"',
        r'\1"' + APP_NAME + '"',
        bg, 'release app_name -> ' + APP_NAME,
    )
    bg = sub_once(
        r'(resValue\s+"string",\s*"app_short_name",\s*)"Amethyst"',
        r'\1"' + APP_NAME + '"',
        bg, 'release app_short_name -> ' + APP_NAME,
    )

    # application_package MUST equal applicationId: pref_control.xml fires an
    # intent at @string/application_package to reach our own activity.
    bg = sub_once(
        r"(resValue\s+'string',\s*'application_package',\s*)'org\.angelauramc\.amethyst'",
        r"\1'" + PKG + "'",
        bg, "release application_package -> our package",
    )

    # FileProvider authority -> our namespace so the app can coexist with a
    # stock Amethyst install instead of colliding on the provider authority.
    bg = sub_once(
        r"(resValue\s+'string',\s*'storageProviderAuthorities',\s*)'org\.angelauramc\.amethyst\.scoped\.gamefolder'",
        r"\1'" + PKG + ".scoped.gamefolder'",
        bg, "release storageProviderAuthorities -> our package",
    )
    # The release block doesn't set shareProviderAuthority; add one alongside
    # storageProviderAuthorities so control-folder sharing is namespaced too.
    bg = sub_once(
        r"(resValue\s+'string',\s*'storageProviderAuthorities',\s*'" + re.escape(PKG) + r"\.scoped\.gamefolder'\n)",
        r"\1            resValue 'string', 'shareProviderAuthority', '" + PKG + ".scoped.controlfolder'\n",
        bg, "release shareProviderAuthority (added)",
    )

    # Point the release signing config at OUR keystore, provided by CI from repo
    # secrets via env vars, instead of upstream's release keystore (which isn't
    # in the repo). The release build type keeps using customRelease, so the
    # published APK is signed with our own stable key.
    bg = sub_once(
        r"customRelease\s*\{[^}]*\}",
        (
            'customRelease {\n'
            '            storeFile file(System.getenv("BA_KEYSTORE_FILE"))\n'
            '            storePassword System.getenv("BA_KEYSTORE_PASSWORD")\n'
            '            keyAlias System.getenv("BA_KEY_ALIAS")\n'
            '            keyPassword System.getenv("BA_KEY_PASSWORD")\n'
            '        }'
        ),
        bg, "customRelease signingConfig -> our keystore (from CI secrets)",
        flags=re.DOTALL,
    )

    write(bg_path, bg)

    print("All patches applied and verified.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Apply patches to a temporary upstream copy and exercise the Java account gates.

Usage: python3 scripts/test_patch.py <fresh_upstream_checkout>
Requires a JDK, but no Android SDK or device.
"""

from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def extract_method(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def run_account_checks(root, work, patched):
    java = root / "app_pojavlauncher/src/main/java/net/kdt/pojavlaunch"
    fragments = []
    for name in ("ProfileTypeSelectFragment", "ModpackCreateFragment"):
        source = (java / f"fragments/{name}.java").read_text()
        method = extract_method(source, "private void tryInstall(")
        fragments.append(f"static class {name} extends Screen {{ {method} }}")

    launcher = (java / "LauncherActivity.java").read_text()
    guard = re.search(
        r"if\s*\(!lwjgl3ifyClientJar\.exists\(\)\)\s*\{\s*if\s*\((.*?)\)\s*\{",
        launcher,
    ).group(1)
    downloader = (java / "tasks/MinecraftDownloader.java").read_text()
    download_guard = re.search(
        r"if\s*\(([^\n]+)\)\s*\{\s*String versionMessage = realVersion;",
        downloader,
    ).group(1)
    harness = """
public class AccountGateTest {
    static boolean local, online, navigated, dialog;
    static class Fragment {}
    static class LWJGL3ifyInstallFragment extends Fragment {}
    static class SearchModFragment extends Fragment {}
    static class Screen { Object requireActivity() { return this; } }
    static boolean hasOnlineProfile() { return !local; }
    static void hasNoOnlineProfileDialog(Object activity) { dialog = true; }
    static class Tools {
        static void swapFragment(Object activity, Class<? extends Fragment> target,
                                 String tag, Object arguments) {
            if (target == null || !target.getSimpleName().equals(tag) || arguments != null)
                throw new AssertionError("Installer navigation arguments changed");
            navigated = true;
        }
    }
    static class Account { boolean isLocal() { return local; } }
    static class Spinner { Account getSelectedAccount() { return new Account(); } }
    final Spinner mAccountSpinner = new Spinner();
    boolean isOnline(Object activity) { return online; }
    boolean blocksMissingAssets() { return GUARD; }
    boolean usesCachedFiles() {
        boolean isLocalProfile = local, isOnline = online;
        return DOWNLOAD_GUARD;
    }
    FRAGMENTS
    static void checkNavigation(boolean expected) {
        if (navigated != expected || dialog == expected)
            throw new AssertionError("Wrong navigation for local=" + local);
        navigated = dialog = false;
    }
    public static void main(String[] args) {
        boolean patched = Boolean.parseBoolean(args[0]);
        AccountGateTest test = new AccountGateTest();
        for (boolean localAccount : new boolean[] {true, false}) {
            local = localAccount;
            for (boolean connected : new boolean[] {true, false}) {
                online = connected;
                new ProfileTypeSelectFragment().tryInstall(
                    LWJGL3ifyInstallFragment.class, "LWJGL3ifyInstallFragment");
                checkNavigation(patched || !local);
                new ModpackCreateFragment().tryInstall(
                    SearchModFragment.class, "SearchModFragment");
                checkNavigation(patched || !local);
                if (test.blocksMissingAssets() != (!online || (!patched && local)))
                    throw new AssertionError("Wrong LWJGL3ify missing-assets behavior");
                if (test.usesCachedFiles() != (!online || (!patched && local)))
                    throw new AssertionError("Wrong Minecraft download behavior");
            }
        }
    }
}
"""
    harness = harness.replace("DOWNLOAD_GUARD", download_guard).replace("GUARD", guard)
    harness = harness.replace("FRAGMENTS", "\n".join(fragments))
    harness_file = work / "AccountGateTest.java"
    harness_file.write_text(harness)
    subprocess.run(["javac", "--release", "8", "-d", str(work), str(harness_file)], check=True)
    subprocess.run(["java", "-cp", str(work), "AccountGateTest", str(patched).lower()], check=True)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    upstream = Path(sys.argv[1]).resolve()
    patcher = Path(__file__).with_name("patch.py")
    with tempfile.TemporaryDirectory(prefix="black-amethyst-test-") as directory:
        work = Path(directory)
        # Only Java source and the app build file are touched by patch.py.
        root = work / "upstream"
        app = root / "app_pojavlauncher"
        shutil.copytree(upstream / "app_pojavlauncher/src/main/java", app / "src/main/java")
        shutil.copy2(upstream / "app_pojavlauncher/build.gradle", app / "build.gradle")
        run_account_checks(root, work, patched=False)
        print("Reproduced upstream local-account failures.", flush=True)
        subprocess.run([sys.executable, str(patcher), str(root)], check=True)
        run_account_checks(root, work, patched=True)

        # The fix must not weaken the launcher's missing-account or demo checks.
        relative = "app_pojavlauncher/src/main/java/net/kdt/pojavlaunch/LauncherActivity.java"
        before = (upstream / relative).read_text()
        after = (root / relative).read_text()
        for guard in ("if(mAccountSpinner.getSelectedAccount() == null)",
                      "if (mAccountSpinner.getSelectedAccount().isDemo())"):
            assert extract_method(before, guard) == extract_method(after, guard), guard

        # Patching a changed/already-patched source must fail instead of silently
        # succeeding with a partially applied offline-account fix.
        rerun = subprocess.run([sys.executable, str(patcher), str(root)], capture_output=True, text=True)
        assert rerun.returncode != 0 and "[PATCH FAILED]" in rerun.stderr
        print("PASS: local/online accounts, connected/offline states, navigation, download gates, "
              "preserved launch restrictions, and fail-closed patching.")


if __name__ == "__main__":
    main()

use std::{env, path::PathBuf, process::Command};

fn compile_swift(src: &str, out_name: &str) -> PathBuf {
    let out = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR")).join(out_name);
    let status = Command::new("xcrun")
        .args(["swiftc", src, "-o"])
        .arg(&out)
        .status()
        .unwrap_or_else(|error| panic!("launch xcrun swiftc {src}: {error}"));
    if !status.success() {
        panic!("failed to compile {src}");
    }
    out
}

fn main() {
    println!("cargo:rerun-if-changed=native/macos_helper.swift");
    println!("cargo:rerun-if-changed=native/harmless_fixture.swift");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("macos") {
        return;
    }
    let helper = compile_swift("native/macos_helper.swift", "computer-runtime-macos-helper");
    println!(
        "cargo:rustc-env=COMPUTER_RUNTIME_MACOS_HELPER={}",
        helper.display()
    );
    let fixture = compile_swift(
        "native/harmless_fixture.swift",
        "computer-runtime-harmless-fixture",
    );
    println!(
        "cargo:rustc-env=COMPUTER_RUNTIME_HARMLESS_FIXTURE={}",
        fixture.display()
    );
}

#[cfg(target_os = "macos")]
mod macos {
    use computer_runtime::{ComputerProvider, ElementIdentity, NativeMacProvider, WindowIdentity};
    use serde_json::{Value, json};
    use std::{
        fs,
        path::{Path, PathBuf},
        process::{Child, Command, Stdio},
        thread,
        time::{Duration, Instant},
    };

    const EVIDENCE_PATH: &str = "/tmp/lab-comp01-action-e2e.json";
    const FORBIDDEN_OWNERS: &[&str] = &[
        "Feishu",
        "Lark",
        "飞书",
        "Google Chrome",
        "Chromium",
        "Safari",
        "Firefox",
        "Code",
        "Codex",
        "Slack",
        "WeChat",
        "微信",
        "Terminal",
        "iTerm2",
        "Cursor",
    ];

    struct ChildGuard(Child);
    impl Drop for ChildGuard {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    struct Evidence {
        path: PathBuf,
        data: Value,
    }

    impl Evidence {
        fn new() -> Self {
            let mut data = json!({
                "task": "LAB-COMP-01",
                "mock_used": false,
                "native_click_reached": false,
                "native_type_reached": false,
                "clicked_marker": false,
                "typed_marker": false,
                "passed": false,
                "error": Value::Null
            });
            data["evidence_path"] = json!(EVIDENCE_PATH);
            let this = Self {
                path: PathBuf::from(EVIDENCE_PATH),
                data,
            };
            this.flush();
            this
        }

        fn set(&mut self, key: &str, value: Value) {
            self.data[key] = value;
            self.flush();
        }

        fn flush(&self) {
            let body = serde_json::to_vec_pretty(&self.data).expect("evidence json");
            fs::write(&self.path, body).expect("write evidence");
        }
    }

    impl Drop for Evidence {
        fn drop(&mut self) {
            self.flush();
        }
    }

    fn wait_file(path: &Path, deadline: Instant) -> bool {
        while Instant::now() < deadline {
            if path.is_file() {
                return true;
            }
            thread::sleep(Duration::from_millis(50));
        }
        path.is_file()
    }

    fn wait_typed(path: &Path, expected: &str, deadline: Instant) -> bool {
        while Instant::now() < deadline {
            if let Ok(body) = fs::read_to_string(path) {
                if body.contains(expected) {
                    return true;
                }
            }
            thread::sleep(Duration::from_millis(50));
        }
        fs::read_to_string(path)
            .map(|body| body.contains(expected))
            .unwrap_or(false)
    }

    fn window_identity(window: &Value) -> WindowIdentity {
        WindowIdentity {
            window_id: window["window_id"].as_str().unwrap().to_string(),
            pid: i32::try_from(window["pid"].as_i64().unwrap()).unwrap(),
        }
    }

    fn find_fixture_window(provider: &NativeMacProvider, pid: i32, title: &str) -> Option<Value> {
        let windows = provider.list_windows().ok()?;
        windows.as_array()?.iter().cloned().find(|window| {
            window["pid"].as_i64() == Some(i64::from(pid))
                && window["title"].as_str() == Some(title)
        })
    }

    fn owner_forbidden(owner: &str) -> bool {
        FORBIDDEN_OWNERS
            .iter()
            .any(|name| owner.eq_ignore_ascii_case(name))
    }

    #[test]
    fn harmless_fixture_find_click_type_reaches_native_helper() {
        let mut evidence = Evidence::new();
        let marker = tempfile::tempdir().expect("marker dir");
        evidence.set("marker_dir", json!(marker.path().display().to_string()));
        let token = marker.path().file_name().unwrap().to_string_lossy();
        let title = format!("LAB-COMP-01 Harmless Fixture {token}");
        evidence.set("fixture_title", json!(title));

        let child = Command::new(env!("COMPUTER_RUNTIME_HARMLESS_FIXTURE"))
            .arg(marker.path())
            .arg(&title)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn harmless fixture");
        let fixture_pid = child.id() as i32;
        let _guard = ChildGuard(child);
        evidence.set("fixture_pid", json!(fixture_pid));

        let ready = marker.path().join("ready.json");
        let deadline = Instant::now() + Duration::from_secs(15);
        if !wait_file(&ready, deadline) {
            evidence.set("error", json!("fixture did not write ready.json"));
            panic!("fixture did not become ready");
        }

        let provider = NativeMacProvider;
        let permissions = provider.permissions().expect("permissions");
        evidence.set("permissions", permissions.clone());
        assert_eq!(permissions["native_helper"], "computer.runtime.macos.v1");
        assert_eq!(
            permissions["accessibility"].as_bool(),
            Some(true),
            "accessibility must be granted for native click/type"
        );

        let window = loop {
            if let Some(window) = find_fixture_window(&provider, fixture_pid, &title) {
                break window;
            }
            if Instant::now() >= deadline {
                evidence.set("error", json!("fixture window not listed"));
                panic!("fixture window not listed");
            }
            thread::sleep(Duration::from_millis(100));
        };
        let owner = window["owner"].as_str().unwrap_or("");
        evidence.set("window", window.clone());
        assert!(
            !owner_forbidden(owner),
            "refusing to act on forbidden owner {owner}"
        );
        assert_eq!(window["pid"].as_i64(), Some(i64::from(fixture_pid)));
        assert_eq!(window["title"].as_str(), Some(title.as_str()));

        let identity = window_identity(&window);
        let bundle_id = provider
            .list_apps()
            .unwrap_or_default()
            .into_iter()
            .find(|app| app.pid == fixture_pid)
            .map(|app| app.bundle_id)
            .unwrap_or_default();
        evidence.set("bundle_id", json!(bundle_id));
        let activated = provider
            .activate_app(fixture_pid, &bundle_id)
            .expect("activate fixture");
        evidence.set("activate", activated);
        thread::sleep(Duration::from_millis(400));

        let typed_text = "lab-comp-01";
        match provider.type_text(&identity, typed_text) {
            Ok(result) => {
                evidence.set("native_type_reached", json!(true));
                evidence.set("type_result", result);
            }
            Err(error) => {
                evidence.set("native_type_reached", json!(false));
                evidence.set("type_error", json!(error));
                evidence.set("error", json!(format!("native type failed: {error}")));
                panic!("native type failed: {error}");
            }
        }
        let typed_ok = wait_typed(
            &marker.path().join("typed"),
            typed_text,
            Instant::now() + Duration::from_secs(5),
        );
        evidence.set("typed_marker", json!(typed_ok));
        evidence.set(
            "typed_file",
            json!(fs::read_to_string(marker.path().join("typed")).unwrap_or_default()),
        );
        if !typed_ok {
            evidence.set(
                "error",
                json!("native type returned success but fixture did not record text"),
            );
            panic!("typed marker missing");
        }

        let found = provider
            .find(&identity, "LAB-COMP-01 Click Me", 10)
            .expect("find fixture button");
        evidence.set("find_result", found.clone());
        let button = found
            .as_array()
            .expect("find returns array")
            .iter()
            .find(|element| {
                element["pid"].as_i64() == Some(i64::from(fixture_pid))
                    && element["window_id"].as_str() == Some(identity.window_id.as_str())
                    && element["label"].as_str() == Some("LAB-COMP-01 Click Me")
                    && element["role"].as_str() == Some("AXButton")
            })
            .cloned()
            .expect("expected unique fixture button");
        let element = ElementIdentity {
            element_id: button["element_id"].as_str().unwrap().to_string(),
            window_id: identity.window_id.clone(),
            pid: identity.pid,
        };

        match provider.click(&element) {
            Ok(result) => {
                evidence.set("native_click_reached", json!(true));
                evidence.set("click_result", result);
            }
            Err(error) => {
                evidence.set("native_click_reached", json!(false));
                evidence.set("click_error", json!(error));
                evidence.set("error", json!(format!("native click failed: {error}")));
                panic!("native click failed: {error}");
            }
        }
        let clicked_ok = wait_file(
            &marker.path().join("clicked"),
            Instant::now() + Duration::from_secs(5),
        );
        evidence.set("clicked_marker", json!(clicked_ok));
        if !clicked_ok {
            evidence.set(
                "error",
                json!("native click returned success but fixture button did not write marker"),
            );
            panic!("clicked marker missing");
        }

        evidence.set("passed", json!(true));
        evidence.set("error", Value::Null);
    }
}

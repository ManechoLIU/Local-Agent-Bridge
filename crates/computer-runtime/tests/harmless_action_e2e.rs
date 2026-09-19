#[cfg(target_os = "macos")]
mod macos {
    use axum::{body::Body, http::Request};
    use computer_runtime::{
        ComputerProvider, ComputerRuntime, ElementIdentity, NativeMacProvider, WindowIdentity,
    };
    use serde_json::{Value, json};
    use sha2::{Digest, Sha256};
    use tower::ServiceExt;
    use std::{
        fs,
        path::{Path, PathBuf},
        process::{Child, Command, Stdio},
        sync::{Mutex, MutexGuard},
        thread,
        time::{Duration, Instant},
    };

    const EVIDENCE_PATH: &str = "/tmp/lab-comp01-action-e2e.json";
    const CANVAS_EVIDENCE_PATH: &str = "/tmp/lab-comp02-canvas-e2e.json";
    static GUI_LOCK: Mutex<()> = Mutex::new(());

    fn lock_native_gui() -> MutexGuard<'static, ()> {
        GUI_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }
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
            Self::with_path(EVIDENCE_PATH, "LAB-COMP-01")
        }

        fn with_path(path: &str, task: &str) -> Self {
            let mut data = json!({
                "task": task,
                "mock_used": false,
                "native_click_reached": false,
                "native_click_at_reached": false,
                "native_type_reached": false,
                "clicked_marker": false,
                "typed_marker": false,
                "passed": false,
                "error": Value::Null
            });
            data["evidence_path"] = json!(path);
            let this = Self {
                path: PathBuf::from(path),
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


    const RUNTIME_KEY: &str = "computer-runtime-backend-key-0123456789abcdef0123456789abcdef";

    fn runtime_request(body: &str, id: &str) -> Request<Body> {
        let digest = Sha256::digest(body.as_bytes());
        let body_sha: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
        Request::builder()
            .method("POST")
            .uri("/mcp")
            .header("content-type", "application/json")
            .header("x-lab-backend-key", RUNTIME_KEY)
            .header("x-lab-capability-id", "cap-computer")
            .header("x-lab-principal-id", "principal-a")
            .header("x-lab-execution-session-id", "exec-fixture")
            .header("x-lab-workspace-id", "workspace-a")
            .header("x-lab-request-id", format!("req-{id}"))
            .header("x-lab-request-nonce", format!("nonce-{id}"))
            .header("x-lab-body-sha256", body_sha)
            .header("x-lab-ingress-provenance", "local_ingress_hmac_v1")
            .header("x-lab-request-mac", "test-mac-from-authenticated-bridge")
            .body(Body::from(body.to_string()))
            .unwrap()
    }

    async fn runtime_call(app: &axum::Router, id: &str, name: &str, arguments: Value) -> Value {
        let body = json!({
            "jsonrpc":"2.0",
            "id":id,
            "method":"tools/call",
            "params":{"name":name,"arguments":arguments}
        })
        .to_string();
        let response = app
            .clone()
            .oneshot(runtime_request(&body, id))
            .await
            .unwrap();
        let status = response.status();
        let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let value: Value = serde_json::from_slice(&bytes).unwrap_or_else(|err| {
            let preview: String = String::from_utf8_lossy(&bytes).chars().take(500).collect();
            panic!("tool {name} returned non-JSON status={status} err={err} body={preview}");
        });
        assert_eq!(status, axum::http::StatusCode::OK, "tool {name} failed: {value}");
        value["result"]["structuredContent"].clone()
    }
    #[test]
    fn harmless_fixture_find_click_type_reaches_native_helper() {
        let _gui = lock_native_gui();
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

    #[tokio::test]
    async fn harmless_fixture_constrained_coordinate_click_reaches_native_helper() {
        let _gui = lock_native_gui();
        let mut evidence = Evidence::new();
        evidence.set("task", json!("LAB-COMP-01-click-at"));
        let marker = tempfile::tempdir().expect("marker dir");
        evidence.set("marker_dir", json!(marker.path().display().to_string()));
        let token = marker.path().file_name().unwrap().to_string_lossy();
        let title = format!("LAB-COMP-01 ClickAt Fixture {token}");
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
            "accessibility must be granted for coordinate click"
        );
        assert_eq!(
            permissions["screen_recording"].as_bool(),
            Some(true),
            "screen recording must be granted for screenshot-bound click"
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
        let identity = window_identity(&window);
        let bundle_id = provider
            .list_apps()
            .unwrap_or_default()
            .into_iter()
            .find(|app| app.pid == fixture_pid)
            .map(|app| app.bundle_id)
            .unwrap_or_default();
        provider
            .activate_app(fixture_pid, &bundle_id)
            .expect("activate fixture");
        thread::sleep(Duration::from_millis(400));

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
                    && element["label"].as_str() == Some("LAB-COMP-01 Click Me")
                    && element["role"].as_str() == Some("AXButton")
            })
            .cloned()
            .expect("expected unique fixture button");
        let button_bounds = button["bounds"].clone();
        let cx = button_bounds["x"].as_f64().unwrap() + button_bounds["width"].as_f64().unwrap() / 2.0;
        let cy = button_bounds["y"].as_f64().unwrap() + button_bounds["height"].as_f64().unwrap() / 2.0;

        let app = ComputerRuntime::new(RUNTIME_KEY.as_bytes(), NativeMacProvider)
            .unwrap()
            .router();
        let opened = runtime_call(&app, "open", "computer_session_open", json!({})).await;
        let session = opened["session_id"].as_str().unwrap().to_string();
        let generation = opened["generation"].as_u64().unwrap();
        let windows = runtime_call(
            &app,
            "windows",
            "computer_list_windows",
            json!({"session_id":session,"expected_session_generation":generation}),
        )
        .await;
        let window_observation = windows["observation_id"].as_str().unwrap().to_string();
        let shot = runtime_call(
            &app,
            "shot",
            "computer_screenshot",
            json!({
                "session_id":session,
                "expected_session_generation":generation,
                "observation_id":window_observation,
                "window_id":identity.window_id,
                "pid":identity.pid,
                "max_width":1280,
                "max_height":720
            }),
        )
        .await;
        evidence.set("screenshot", json!({
            "observation_id": shot["observation_id"],
            "width": shot["width"],
            "height": shot["height"],
            "bounds": shot["bounds"],
            "image_sha256": shot["image_sha256"]
        }));
        let shot_observation = shot["observation_id"].as_str().unwrap().to_string();
        let bounds = &shot["bounds"];
        let img_w = shot["width"].as_f64().unwrap();
        let img_h = shot["height"].as_f64().unwrap();
        let win_w = bounds["width"].as_f64().unwrap();
        let win_h = bounds["height"].as_f64().unwrap();
        let image_x = ((cx - bounds["x"].as_f64().unwrap()) / win_w * img_w).floor() as i64;
        let image_y = ((cy - bounds["y"].as_f64().unwrap()) / win_h * img_h).floor() as i64;
        evidence.set("mapped_click", json!({"image_x":image_x,"image_y":image_y,"cx":cx,"cy":cy}));
        assert!(image_x >= 0 && image_y >= 0, "mapped click left the screenshot");
        assert!(
            (image_x as f64) < img_w && (image_y as f64) < img_h,
            "mapped click left the screenshot"
        );

        let clicked = runtime_call(
            &app,
            "click-at",
            "computer_click_at",
            json!({
                "session_id":session,
                "expected_session_generation":generation,
                "observation_id":shot_observation,
                "window_id":identity.window_id,
                "pid":identity.pid,
                "image_x":image_x,
                "image_y":image_y
            }),
        )
        .await;
        evidence.set("native_click_at_reached", json!(true));
        evidence.set("click_at_result", clicked);
        let clicked_ok = wait_file(
            &marker.path().join("clicked"),
            Instant::now() + Duration::from_secs(5),
        );
        evidence.set("clicked_marker", json!(clicked_ok));
        if !clicked_ok {
            evidence.set(
                "error",
                json!("coordinate click returned success but fixture button did not write marker"),
            );
            panic!("clicked marker missing after computer_click_at");
        }
        evidence.set("passed", json!(true));
        evidence.set("error", Value::Null);
    }


    #[tokio::test]
    async fn canvas_fixture_coordinate_click_without_ax_reaches_native_helper() {
        let _gui = lock_native_gui();
        let mut evidence = Evidence::with_path(CANVAS_EVIDENCE_PATH, "LAB-COMP-02-canvas");
        let marker = tempfile::tempdir().expect("marker dir");
        let token = marker.path().file_name().unwrap().to_string_lossy();
        let title = format!("LAB-COMP-02 Canvas Fixture {token}");
        evidence.set("fixture_title", json!(title));

        let child = Command::new(env!("COMPUTER_RUNTIME_CANVAS_FIXTURE"))
            .arg(marker.path())
            .arg(&title)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn canvas fixture");
        let fixture_pid = child.id() as i32;
        let _guard = ChildGuard(child);
        evidence.set("fixture_pid", json!(fixture_pid));

        let ready = marker.path().join("ready.json");
        let deadline = Instant::now() + Duration::from_secs(15);
        if !wait_file(&ready, deadline) {
            evidence.set("error", json!("canvas fixture did not write ready.json"));
            panic!("canvas fixture did not become ready");
        }

        let provider = NativeMacProvider;
        let permissions = provider.permissions().expect("permissions");
        assert_eq!(permissions["accessibility"].as_bool(), Some(true));
        assert_eq!(permissions["screen_recording"].as_bool(), Some(true));

        let window = loop {
            if let Some(window) = find_fixture_window(&provider, fixture_pid, &title) {
                break window;
            }
            if Instant::now() >= deadline {
                panic!("canvas fixture window not listed");
            }
            thread::sleep(Duration::from_millis(100));
        };
        evidence.set("window", window.clone());
        let identity = window_identity(&window);
        let bundle_id = provider
            .list_apps()
            .unwrap_or_default()
            .into_iter()
            .find(|app| app.pid == fixture_pid)
            .map(|app| app.bundle_id)
            .unwrap_or_default();
        provider
            .activate_app(fixture_pid, &bundle_id)
            .expect("activate canvas");
        thread::sleep(Duration::from_millis(400));

        let found = provider.find(&identity, "LAB-COMP-01 Click Me", 5);
        let ax_find = found
            .ok()
            .and_then(|v| v.as_array().cloned())
            .unwrap_or_default();
        evidence.set("ax_find", json!(ax_find));
        assert!(
            ax_find.is_empty(),
            "canvas fixture must not expose an AX click target, got {ax_find:?}"
        );

        let app = ComputerRuntime::new(RUNTIME_KEY.as_bytes(), NativeMacProvider)
            .unwrap()
            .router();
        let opened = runtime_call(&app, "open", "computer_session_open", json!({})).await;
        let session = opened["session_id"].as_str().unwrap().to_string();
        let generation = opened["generation"].as_u64().unwrap();
        let windows = runtime_call(
            &app,
            "windows",
            "computer_list_windows",
            json!({"session_id":session,"expected_session_generation":generation}),
        )
        .await;
        let window_observation = windows["observation_id"].as_str().unwrap().to_string();
        let shot = runtime_call(
            &app,
            "shot",
            "computer_screenshot",
            json!({
                "session_id":session,
                "expected_session_generation":generation,
                "observation_id":window_observation,
                "window_id":identity.window_id,
                "pid":identity.pid,
                "max_width":800,
                "max_height":600
            }),
        )
        .await;
        evidence.set(
            "screenshot",
            json!({
                "observation_id": shot["observation_id"],
                "width": shot["width"],
                "height": shot["height"],
                "scale_x": shot["scale_x"],
                "scale_y": shot["scale_y"],
                "bounds": shot["bounds"]
            }),
        );
        let ready_json: Value = serde_json::from_slice(&fs::read(&ready).expect("read ready.json"))
            .expect("parse ready.json");
        let content_w = ready_json["content"]["width"].as_f64().unwrap();
        let content_h = ready_json["content"]["height"].as_f64().unwrap();
        let tx = ready_json["target"]["x"].as_f64().unwrap();
        let ty = ready_json["target"]["y"].as_f64().unwrap();
        let tw = ready_json["target"]["width"].as_f64().unwrap();
        let th = ready_json["target"]["height"].as_f64().unwrap();
        let bounds = &shot["bounds"];
        let win_w = bounds["width"].as_f64().unwrap();
        let win_h = bounds["height"].as_f64().unwrap();
        let img_w = shot["width"].as_f64().unwrap();
        let img_h = shot["height"].as_f64().unwrap();
        let title_bar = (win_h - content_h).max(0.0);
        let local_x = tx + tw / 2.0;
        let y_from_top_of_content = content_h - (ty + th / 2.0);
        let y_from_top_of_window = title_bar + y_from_top_of_content;
        let image_x = ((local_x / win_w) * img_w).floor() as i64;
        let image_y = ((y_from_top_of_window / win_h) * img_h).floor() as i64;
        evidence.set(
            "mapped_click",
            json!({
                "image_x": image_x,
                "image_y": image_y,
                "content": ready_json["content"],
                "target": ready_json["target"],
                "title_bar": title_bar
            }),
        );
        assert!(image_x >= 0 && image_y >= 0, "mapped canvas click left the screenshot");
        assert!(
            (image_x as f64) < img_w && (image_y as f64) < img_h,
            "mapped canvas click left the screenshot"
        );
        let _ = content_w;
        let clicked = runtime_call(
            &app,
            "click-at",
            "computer_click_at",
            json!({
                "session_id":session,
                "expected_session_generation":generation,
                "observation_id":shot["observation_id"],
                "window_id":identity.window_id,
                "pid":identity.pid,
                "image_x":image_x,
                "image_y":image_y
            }),
        )
        .await;
        evidence.set("native_click_at_reached", json!(true));
        evidence.set("mock_used", json!(false));
        evidence.set("click_at_result", clicked.clone());
        let mut clicked_ok = wait_file(
            &marker.path().join("clicked"),
            Instant::now() + Duration::from_secs(2),
        );
        if !clicked_ok {
            let retried = runtime_call(
                &app,
                "click-at-retry",
                "computer_click_at",
                json!({
                    "session_id":session,
                    "expected_session_generation":generation,
                    "observation_id":shot["observation_id"],
                    "window_id":identity.window_id,
                    "pid":identity.pid,
                    "image_x":image_x,
                    "image_y":image_y
                }),
            )
            .await;
            evidence.set("click_at_retry", retried);
            clicked_ok = wait_file(
                &marker.path().join("clicked"),
                Instant::now() + Duration::from_secs(5),
            );
        }
        evidence.set("clicked_marker", json!(clicked_ok));
        if !clicked_ok {
            evidence.set("error", json!("canvas coordinate click did not write marker"));
            panic!("canvas fixture was not clicked without AX");
        }
        assert_eq!(evidence.data["mock_used"], json!(false));
        assert_eq!(evidence.data["native_click_at_reached"], json!(true));
        evidence.set("passed", json!(true));
        evidence.set("error", Value::Null);
    }

}

use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use computer_runtime::{
    AppInfo, ComputerProvider, ComputerRuntime, ElementIdentity, WindowIdentity,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};
use tower::ServiceExt;

const KEY: &str = "computer-runtime-backend-key-0123456789abcdef0123456789abcdef";

#[derive(Clone)]
struct MockProvider {
    actions: Arc<Mutex<Vec<String>>>,
    permissions: Arc<Mutex<Value>>,
}

impl Default for MockProvider {
    fn default() -> Self {
        Self {
            actions: Arc::new(Mutex::new(Vec::new())),
            permissions: Arc::new(Mutex::new(json!({
                "platform":"mock",
                "accessibility":true,
                "screen_recording":true
            }))),
        }
    }
}

impl MockProvider {
    fn with_permissions(accessibility: bool, screen_recording: bool) -> Self {
        Self {
            permissions: Arc::new(Mutex::new(json!({
                "platform":"mock",
                "accessibility":accessibility,
                "screen_recording":screen_recording
            }))),
            ..Self::default()
        }
    }
}

impl ComputerProvider for MockProvider {
    fn permissions(&self) -> Result<Value, String> {
        Ok(self.permissions.lock().unwrap().clone())
    }

    fn list_apps(&self) -> Result<Vec<AppInfo>, String> {
        Ok(vec![AppInfo {
            pid: 4242,
            bundle_id: "com.example.fixture".into(),
            display_name: "Fixture".into(),
            is_active: false,
            is_hidden: false,
            is_terminated: false,
        }])
    }

    fn list_windows(&self) -> Result<Value, String> {
        Ok(json!([{
            "window_id":"wid_fixture",
            "pid":4242,
            "title":"Fixture",
            "bounds":{"x":10,"y":20,"width":800,"height":600}
        }]))
    }

    fn active_window(&self) -> Result<Value, String> {
        Ok(json!({
            "window_id":"wid_fixture",
            "pid":4242,
            "title":"Fixture",
            "bounds":{"x":10,"y":20,"width":800,"height":600}
        }))
    }

    fn activate_app(&self, pid: i32, bundle_id: &str) -> Result<Value, String> {
        self.actions
            .lock()
            .unwrap()
            .push(format!("activate:{pid}:{bundle_id}"));
        Ok(json!({"activated":true,"pid":pid,"bundle_id":bundle_id}))
    }

    fn screenshot(
        &self,
        window: &WindowIdentity,
        max_width: u32,
        max_height: u32,
    ) -> Result<Value, String> {
        self.actions.lock().unwrap().push(format!(
            "screenshot:{}:{}:{max_width}x{max_height}",
            window.window_id, window.pid
        ));
        Ok(json!({
            "window_id":window.window_id,
            "pid":window.pid,
            "width":max_width,
            "height":max_height,
            "mime_type":"image/png",
            "bytes":3,
            "data_base64":"YWJj",
            "image_sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            "bounds":{"x":10,"y":20,"width":800,"height":600}
        }))
    }

    fn find(
        &self,
        window: &WindowIdentity,
        query: &str,
        max_results: u32,
    ) -> Result<Value, String> {
        self.actions.lock().unwrap().push(format!(
            "find:{}:{}:{query}:{max_results}",
            window.window_id, window.pid
        ));
        Ok(json!([{
            "element_id":"axp:0:fixture",
            "window_id":window.window_id,
            "pid":window.pid,
            "role":"AXButton",
            "label":"Fixture Button"
        }]))
    }

    fn click(&self, element: &ElementIdentity) -> Result<Value, String> {
        self.actions.lock().unwrap().push(format!(
            "click:{}:{}:{}",
            element.element_id, element.window_id, element.pid
        ));
        Ok(json!({"clicked":true}))
    }

    fn click_xy(
        &self,
        window: &WindowIdentity,
        x: f64,
        y: f64,
        button: &str,
    ) -> Result<Value, String> {
        self.actions.lock().unwrap().push(format!(
            "click_xy:{}:{}:{x:.3}:{y:.3}:{button}",
            window.window_id, window.pid
        ));
        Ok(json!({"clicked":true,"x":x,"y":y,"button":button}))
    }
    fn list_displays(&self) -> Result<Value, String> {
        self.actions.lock().unwrap().push("list_displays".into());
        Ok(json!([{"display_id":1,"main":true,"bounds":{"x":0,"y":0,"width":1440,"height":900},"scale":2.0}]))
    }
    fn focus_window(&self, window: &WindowIdentity) -> Result<Value, String> {
        self.actions.lock().unwrap().push(format!("focus:{}:{}", window.window_id, window.pid));
        Ok(json!({"focused":true}))
    }
    fn move_xy(&self, window: &WindowIdentity, x: f64, y: f64) -> Result<Value, String> {
        self.actions.lock().unwrap().push(format!("move_xy:{}:{}:{x:.3}:{y:.3}", window.window_id, window.pid));
        Ok(json!({"moved":true,"x":x,"y":y}))
    }
    fn drag_xy(&self, window: &WindowIdentity, x1: f64, y1: f64, x2: f64, y2: f64) -> Result<Value, String> {
        self.actions.lock().unwrap().push(format!("drag_xy:{}:{}:{x1:.3}:{y1:.3}:{x2:.3}:{y2:.3}", window.window_id, window.pid));
        Ok(json!({"dragged":true}))
    }
    fn paste(&self, window: &WindowIdentity, text: &str) -> Result<Value, String> {
        self.actions.lock().unwrap().push(format!("paste:{}:{}:{}", window.window_id, window.pid, text.chars().count()));
        Ok(json!({"pasted":true,"characters":text.chars().count()}))
    }
    fn set_value(&self, element: &ElementIdentity, value: &str) -> Result<Value, String> {
        self.actions.lock().unwrap().push(format!("set_value:{}:{}", element.element_id, value.chars().count()));
        Ok(json!({"set":true}))
    }

    fn type_text(&self, window: &WindowIdentity, text: &str) -> Result<Value, String> {
        self.actions.lock().unwrap().push(format!(
            "type:{}:{}:{}",
            window.window_id,
            window.pid,
            text.chars().count()
        ));
        Ok(json!({"typed":true,"characters":text.chars().count()}))
    }

    fn key(
        &self,
        window: &WindowIdentity,
        key: &str,
        modifiers: &[String],
    ) -> Result<Value, String> {
        self.actions.lock().unwrap().push(format!(
            "key:{}:{}:{}:{}",
            window.window_id,
            window.pid,
            key,
            modifiers.join("+")
        ));
        Ok(json!({"keyed":true}))
    }

    fn scroll(&self, window: &WindowIdentity, delta_x: i32, delta_y: i32) -> Result<Value, String> {
        self.actions.lock().unwrap().push(format!(
            "scroll:{}:{}:{delta_x}:{delta_y}",
            window.window_id, window.pid
        ));
        Ok(json!({"scrolled":true}))
    }
}

fn bridge_request(body: &str, key: &str, execution: &str) -> Request<Body> {
    let digest = Sha256::digest(body.as_bytes());
    let body_sha: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("x-lab-backend-key", key)
        .header("x-lab-capability-id", "cap-computer")
        .header("x-lab-principal-id", "principal-a")
        .header("x-lab-execution-session-id", execution)
        .header("x-lab-workspace-id", "workspace-a")
        .header("x-lab-request-id", format!("req-{execution}"))
        .header("x-lab-request-nonce", format!("nonce-{execution}"))
        .header("x-lab-body-sha256", body_sha)
        .header("x-lab-ingress-provenance", "local_ingress_hmac_v1")
        .header("x-lab-request-mac", "test-mac-from-authenticated-bridge")
        .body(Body::from(body.to_string()))
        .unwrap()
}

async fn body_json(response: axum::response::Response) -> Value {
    let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

async fn call(
    app: &axum::Router,
    execution: &str,
    id: &str,
    name: &str,
    arguments: Value,
) -> axum::response::Response {
    let body = json!({
        "jsonrpc":"2.0",
        "id":id,
        "method":"tools/call",
        "params":{"name":name,"arguments":arguments}
    })
    .to_string();
    app.clone()
        .oneshot(bridge_request(&body, KEY, execution))
        .await
        .unwrap()
}

async fn open_session(app: &axum::Router, execution: &str) -> (String, u64) {
    let response = call(app, execution, "open", "computer_session_open", json!({})).await;
    assert_eq!(response.status(), StatusCode::OK);
    let value = body_json(response).await;
    (
        value["result"]["structuredContent"]["session_id"]
            .as_str()
            .unwrap()
            .to_string(),
        value["result"]["structuredContent"]["generation"]
            .as_u64()
            .unwrap(),
    )
}

async fn observation_id(response: axum::response::Response) -> String {
    assert_eq!(response.status(), StatusCode::OK);
    let value = body_json(response).await;
    value["result"]["structuredContent"]["observation_id"]
        .as_str()
        .unwrap()
        .to_string()
}

#[tokio::test]
async fn backend_key_and_signed_bridge_context_are_both_required() {
    let app = ComputerRuntime::new(KEY.as_bytes(), MockProvider::default())
        .unwrap()
        .router();
    let body = r#"{"jsonrpc":"2.0","id":"open","method":"tools/call","params":{"name":"computer_session_open","arguments":{}}}"#;

    let bad_key = app
        .clone()
        .oneshot(bridge_request(
            body,
            "wrong-wrong-wrong-wrong-wrong-wrong",
            "exec-a",
        ))
        .await
        .unwrap();
    assert_eq!(bad_key.status(), StatusCode::UNAUTHORIZED);

    let missing_bridge_context = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("x-lab-backend-key", KEY)
        .body(Body::from(body))
        .unwrap();
    let response = app.oneshot(missing_bridge_context).await.unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn computer_session_ownership_and_generation_fail_closed() {
    let app = ComputerRuntime::new(KEY.as_bytes(), MockProvider::default())
        .unwrap()
        .router();
    let (session, generation) = open_session(&app, "exec-a").await;

    let other_execution = call(
        &app,
        "exec-b",
        "apps-other",
        "computer_list_apps",
        json!({"session_id":session,"expected_session_generation":generation}),
    )
    .await;
    assert_eq!(other_execution.status(), StatusCode::FORBIDDEN);

    let stale_generation = call(
        &app,
        "exec-a",
        "apps-stale",
        "computer_list_apps",
        json!({"session_id":session,"expected_session_generation":generation + 1}),
    )
    .await;
    assert_eq!(stale_generation.status(), StatusCode::FORBIDDEN);

    let missing_generation = call(
        &app,
        "exec-a",
        "apps-missing",
        "computer_list_apps",
        json!({"session_id":session}),
    )
    .await;
    assert_eq!(missing_generation.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn bounded_activate_requires_target_from_same_fresh_observation() {
    let provider = MockProvider::default();
    let actions = Arc::clone(&provider.actions);
    let app = ComputerRuntime::new(KEY.as_bytes(), provider)
        .unwrap()
        .router();
    let (session, generation) = open_session(&app, "exec-a").await;

    let observation = observation_id(
        call(
            &app,
            "exec-a",
            "apps",
            "computer_list_apps",
            json!({"session_id":session,"expected_session_generation":generation}),
        )
        .await,
    )
    .await;

    let forbidden = call(
        &app,
        "exec-a",
        "activate-forged",
        "computer_activate_app",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":observation,
            "pid":9999,
            "bundle_id":"com.example.other"
        }),
    )
    .await;
    assert_eq!(forbidden.status(), StatusCode::FORBIDDEN);
    assert!(actions.lock().unwrap().is_empty());

    let allowed = call(
        &app,
        "exec-a",
        "activate",
        "computer_activate_app",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":observation,
            "pid":4242,
            "bundle_id":"com.example.fixture"
        }),
    )
    .await;
    assert_eq!(allowed.status(), StatusCode::OK);
    assert_eq!(
        actions.lock().unwrap().as_slice(),
        &["activate:4242:com.example.fixture".to_string()]
    );
}

#[tokio::test]
async fn screenshot_find_click_type_key_scroll_are_window_and_element_observation_bound() {
    let provider = MockProvider::default();
    let actions = Arc::clone(&provider.actions);
    let app = ComputerRuntime::new(KEY.as_bytes(), provider)
        .unwrap()
        .router();
    let (session, generation) = open_session(&app, "exec-a").await;

    let window_observation = observation_id(
        call(
            &app,
            "exec-a",
            "windows",
            "computer_list_windows",
            json!({"session_id":session,"expected_session_generation":generation}),
        )
        .await,
    )
    .await;

    let common = json!({
        "session_id":session,
        "expected_session_generation":generation,
        "observation_id":window_observation,
        "window_id":"wid_fixture",
        "pid":4242
    });

    let mut screenshot_args = common.clone();
    screenshot_args["max_width"] = json!(640);
    screenshot_args["max_height"] = json!(480);
    let screenshot = call(
        &app,
        "exec-a",
        "shot",
        "computer_screenshot",
        screenshot_args,
    )
    .await;
    assert_eq!(screenshot.status(), StatusCode::OK);

    let mut find_args = common.clone();
    find_args["query"] = json!("Fixture");
    find_args["max_results"] = json!(5);
    let find_response = call(&app, "exec-a", "find", "computer_find", find_args).await;
    let find_value = body_json(find_response).await;
    let element_observation = find_value["result"]["structuredContent"]["observation_id"]
        .as_str()
        .unwrap()
        .to_string();

    let click = call(
        &app,
        "exec-a",
        "click",
        "computer_click",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":element_observation,
            "element_id":"axp:0:fixture",
            "window_id":"wid_fixture",
            "pid":4242
        }),
    )
    .await;
    assert_eq!(click.status(), StatusCode::OK);

    let forged_click = call(
        &app,
        "exec-a",
        "click-forged",
        "computer_click",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":element_observation,
            "element_id":"axp:999:forged",
            "window_id":"wid_fixture",
            "pid":4242
        }),
    )
    .await;
    assert_eq!(forged_click.status(), StatusCode::FORBIDDEN);

    let mut type_args = common.clone();
    type_args["text"] = json!("hello");
    assert_eq!(
        call(&app, "exec-a", "type", "computer_type", type_args)
            .await
            .status(),
        StatusCode::OK
    );

    let mut key_args = common.clone();
    key_args["key"] = json!("enter");
    key_args["modifiers"] = json!(["shift"]);
    assert_eq!(
        call(&app, "exec-a", "key", "computer_key", key_args)
            .await
            .status(),
        StatusCode::OK
    );

    let mut scroll_args = common.clone();
    scroll_args["delta_x"] = json!(0);
    scroll_args["delta_y"] = json!(-120);
    assert_eq!(
        call(&app, "exec-a", "scroll", "computer_scroll", scroll_args)
            .await
            .status(),
        StatusCode::OK
    );

    let calls = actions.lock().unwrap().clone();
    assert!(calls.iter().any(|value| value.starts_with("screenshot:")));
    assert!(calls.iter().any(|value| value.starts_with("find:")));
    assert!(calls.iter().any(|value| value.starts_with("click:")));
    assert!(calls.iter().any(|value| value == "type:wid_fixture:4242:5"));
    assert!(
        calls
            .iter()
            .any(|value| value == "key:wid_fixture:4242:enter:shift")
    );
    assert!(
        calls
            .iter()
            .any(|value| value == "scroll:wid_fixture:4242:0:-120")
    );
    assert_eq!(
        calls
            .iter()
            .filter(|value| value.starts_with("click:"))
            .count(),
        1,
        "forged element must fail before provider side effect"
    );
}

#[tokio::test]
async fn constrained_coordinate_click_binds_fresh_screenshot_observation() {
    let provider = MockProvider::default();
    let actions = Arc::clone(&provider.actions);
    let app = ComputerRuntime::new(KEY.as_bytes(), provider)
        .unwrap()
        .router();
    let (session, generation) = open_session(&app, "exec-a").await;

    let window_observation = observation_id(
        call(
            &app,
            "exec-a",
            "windows",
            "computer_list_windows",
            json!({"session_id":session,"expected_session_generation":generation}),
        )
        .await,
    )
    .await;

    let screenshot = call(
        &app,
        "exec-a",
        "shot",
        "computer_screenshot",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":window_observation,
            "window_id":"wid_fixture",
            "pid":4242,
            "max_width":640,
            "max_height":480
        }),
    )
    .await;
    assert_eq!(screenshot.status(), StatusCode::OK);
    let shot = body_json(screenshot).await["result"]["structuredContent"].clone();
    let shot_observation = shot["observation_id"].as_str().unwrap().to_string();
    assert_eq!(
        shot["image_sha256"],
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    );
    assert_eq!(shot["width"], 640);
    assert_eq!(shot["height"], 480);

    let click = call(
        &app,
        "exec-a",
        "click-at",
        "computer_click_at",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":shot_observation,
            "window_id":"wid_fixture",
            "pid":4242,
            "image_x":320,
            "image_y":240
        }),
    )
    .await;
    assert_eq!(click.status(), StatusCode::OK);
    let click_value = body_json(click).await;
    let click_obs = click_value["result"]["structuredContent"]["observation_id"]
        .as_str()
        .unwrap();
    assert_ne!(click_obs, shot_observation, "click_at must re-observe");
    let calls = actions.lock().unwrap().clone();
    assert!(
        calls.iter().any(|value| value == "click_xy:wid_fixture:4242:410.625:320.625:left"),
        "expected mapped coordinate click, got {calls:?}"
    );
    assert!(
        calls.iter().filter(|value| value.starts_with("screenshot:")).count() >= 2,
        "click_at must capture a follow-up observation, got {calls:?}"
    );

    let out_of_bounds = call(
        &app,
        "exec-a",
        "click-oob",
        "computer_click_at",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":shot_observation,
            "window_id":"wid_fixture",
            "pid":4242,
            "image_x":640,
            "image_y":0
        }),
    )
    .await;
    assert_eq!(out_of_bounds.status(), StatusCode::FORBIDDEN);

    let wrong_window = call(
        &app,
        "exec-a",
        "click-wrong-window",
        "computer_click_at",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":shot_observation,
            "window_id":"wid_other",
            "pid":4242,
            "image_x":1,
            "image_y":1
        }),
    )
    .await;
    assert_eq!(wrong_window.status(), StatusCode::FORBIDDEN);

    let no_frame = call(
        &app,
        "exec-a",
        "click-no-frame",
        "computer_click_at",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":window_observation,
            "window_id":"wid_fixture",
            "pid":4242,
            "image_x":1,
            "image_y":1
        }),
    )
    .await;
    assert_eq!(no_frame.status(), StatusCode::FORBIDDEN);

    assert_eq!(
        actions
            .lock()
            .unwrap()
            .iter()
            .filter(|value| value.starts_with("click_xy:"))
            .count(),
        1,
        "fail-closed click_at must not reach the provider"
    );
}

#[tokio::test]
async fn constrained_coordinate_click_maps_retina_pixels_through_logical_window_bounds() {
    let provider = MockProvider::default();
    let actions = Arc::clone(&provider.actions);
    let app = ComputerRuntime::new(KEY.as_bytes(), provider)
        .unwrap()
        .router();
    let (session, generation) = open_session(&app, "exec-a").await;
    let window_observation = observation_id(
        call(
            &app,
            "exec-a",
            "windows",
            "computer_list_windows",
            json!({"session_id":session,"expected_session_generation":generation}),
        )
        .await,
    )
    .await;
    let shot = body_json(
        call(
            &app,
            "exec-a",
            "shot",
            "computer_screenshot",
            json!({
                "session_id":session,
                "expected_session_generation":generation,
                "observation_id":window_observation,
                "window_id":"wid_fixture",
                "pid":4242,
                "max_width":1600,
                "max_height":1200
            }),
        )
        .await,
    )
    .await["result"]["structuredContent"]
        .clone();
    assert_eq!(shot["width"], 1600);
    assert_eq!(shot["height"], 1200);
    assert_eq!(shot["scale_x"], 0.5);
    assert_eq!(shot["scale_y"], 0.5);
    let click = call(
        &app,
        "exec-a",
        "click-retina",
        "computer_click_at",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":shot["observation_id"],
            "window_id":"wid_fixture",
            "pid":4242,
            "image_x":800,
            "image_y":600
        }),
    )
    .await;
    assert_eq!(click.status(), StatusCode::OK);
    let calls = actions.lock().unwrap().clone();
    assert!(
        calls.iter().any(|value| value == "click_xy:wid_fixture:4242:410.250:320.250:left"),
        "expected retina mapping, got {calls:?}"
    );
}

#[tokio::test]
async fn helper_parity_actions_are_observation_bound() {
    let provider = MockProvider::default();
    let actions = Arc::clone(&provider.actions);
    let app = ComputerRuntime::new(KEY.as_bytes(), provider).unwrap().router();
    let (session, generation) = open_session(&app, "exec-a").await;
    let window_observation = observation_id(
        call(&app, "exec-a", "windows", "computer_list_windows", json!({"session_id":session,"expected_session_generation":generation})).await,
    ).await;
    let displays = call(&app, "exec-a", "displays", "computer_list_displays", json!({"session_id":session,"expected_session_generation":generation})).await;
    assert_eq!(displays.status(), StatusCode::OK);
    let shot = body_json(call(&app, "exec-a", "shot", "computer_screenshot", json!({
        "session_id":session,"expected_session_generation":generation,"observation_id":window_observation,
        "window_id":"wid_fixture","pid":4242,"max_width":640,"max_height":480
    })).await).await["result"]["structuredContent"].clone();
    let shot_obs = shot["observation_id"].as_str().unwrap();
    assert_eq!(call(&app, "exec-a", "focus", "computer_focus_window", json!({
        "session_id":session,"expected_session_generation":generation,"observation_id":window_observation,
        "window_id":"wid_fixture","pid":4242
    })).await.status(), StatusCode::OK);
    assert_eq!(call(&app, "exec-a", "move", "computer_move_at", json!({
        "session_id":session,"expected_session_generation":generation,"observation_id":shot_obs,
        "window_id":"wid_fixture","pid":4242,"image_x":10,"image_y":10
    })).await.status(), StatusCode::OK);
    assert_eq!(call(&app, "exec-a", "drag", "computer_drag_at", json!({
        "session_id":session,"expected_session_generation":generation,"observation_id":shot_obs,
        "window_id":"wid_fixture","pid":4242,"from_image_x":10,"from_image_y":10,"to_image_x":20,"to_image_y":20
    })).await.status(), StatusCode::OK);
    assert_eq!(call(&app, "exec-a", "paste", "computer_paste", json!({
        "session_id":session,"expected_session_generation":generation,"observation_id":window_observation,
        "window_id":"wid_fixture","pid":4242,"text":"hello"
    })).await.status(), StatusCode::OK);
    let find = body_json(call(&app, "exec-a", "find", "computer_find", json!({
        "session_id":session,"expected_session_generation":generation,"observation_id":window_observation,
        "window_id":"wid_fixture","pid":4242,"query":"Fixture","max_results":5
    })).await).await;
    let el_obs = find["result"]["structuredContent"]["observation_id"].as_str().unwrap();
    assert_eq!(call(&app, "exec-a", "set", "computer_set_value", json!({
        "session_id":session,"expected_session_generation":generation,"observation_id":el_obs,
        "element_id":"axp:0:fixture","window_id":"wid_fixture","pid":4242,"value":"abc"
    })).await.status(), StatusCode::OK);
    assert_eq!(call(&app, "exec-a", "right", "computer_click_at", json!({
        "session_id":session,"expected_session_generation":generation,"observation_id":shot_obs,
        "window_id":"wid_fixture","pid":4242,"image_x":11,"image_y":11,"button":"right"
    })).await.status(), StatusCode::OK);
    let forged = call(&app, "exec-a", "drag-oob", "computer_drag_at", json!({
        "session_id":session,"expected_session_generation":generation,"observation_id":shot_obs,
        "window_id":"wid_fixture","pid":4242,"from_image_x":10,"from_image_y":10,"to_image_x":640,"to_image_y":0
    })).await;
    assert_eq!(forged.status(), StatusCode::FORBIDDEN);
    let calls = actions.lock().unwrap().clone();
    assert!(calls.iter().any(|v| v == "focus:wid_fixture:4242"));
    assert!(calls.iter().any(|v| v.starts_with("move_xy:")));
    assert!(calls.iter().any(|v| v.starts_with("drag_xy:")));
    assert!(calls.iter().any(|v| v.starts_with("paste:")));
    assert!(calls.iter().any(|v| v.starts_with("set_value:")));
    assert!(calls.iter().any(|v| v.ends_with(":right")));
}

#[tokio::test]
async fn constrained_coordinate_click_rejects_expired_screenshot_observation() {
    let provider = MockProvider::default();
    let actions = Arc::clone(&provider.actions);
    let app = ComputerRuntime::new_with_observation_limits(
        KEY.as_bytes(),
        provider,
        Duration::from_millis(5),
        8,
    )
    .unwrap()
    .router();
    let (session, generation) = open_session(&app, "exec-a").await;
    let window_observation = observation_id(
        call(
            &app,
            "exec-a",
            "windows",
            "computer_list_windows",
            json!({"session_id":session,"expected_session_generation":generation}),
        )
        .await,
    )
    .await;
    let shot_observation = observation_id(
        call(
            &app,
            "exec-a",
            "shot",
            "computer_screenshot",
            json!({
                "session_id":session,
                "expected_session_generation":generation,
                "observation_id":window_observation,
                "window_id":"wid_fixture",
                "pid":4242,
                "max_width":640,
                "max_height":480
            }),
        )
        .await,
    )
    .await;
    std::thread::sleep(Duration::from_millis(12));
    let expired = call(
        &app,
        "exec-a",
        "click-expired",
        "computer_click_at",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":shot_observation,
            "window_id":"wid_fixture",
            "pid":4242,
            "image_x":1,
            "image_y":1
        }),
    )
    .await;
    assert_eq!(expired.status(), StatusCode::FORBIDDEN);
    assert!(
        !actions
            .lock()
            .unwrap()
            .iter()
            .any(|value| value.starts_with("click_xy:"))
    );
}

#[tokio::test]
async fn input_permission_and_argument_bounds_fail_before_provider_side_effect() {
    let provider = MockProvider::with_permissions(false, true);
    let actions = Arc::clone(&provider.actions);
    let app = ComputerRuntime::new(KEY.as_bytes(), provider)
        .unwrap()
        .router();
    let (session, generation) = open_session(&app, "exec-a").await;
    let window_observation = observation_id(
        call(
            &app,
            "exec-a",
            "windows",
            "computer_list_windows",
            json!({"session_id":session,"expected_session_generation":generation}),
        )
        .await,
    )
    .await;

    let denied = call(
        &app,
        "exec-a",
        "find",
        "computer_find",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":window_observation,
            "window_id":"wid_fixture",
            "pid":4242,
            "query":"Fixture",
            "max_results":5
        }),
    )
    .await;
    assert_eq!(denied.status(), StatusCode::FORBIDDEN);
    assert!(actions.lock().unwrap().is_empty());

    let out_of_bounds = call(
        &app,
        "exec-a",
        "scroll",
        "computer_scroll",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":window_observation,
            "window_id":"wid_fixture",
            "pid":4242,
            "delta_x":0,
            "delta_y":2001
        }),
    )
    .await;
    assert_eq!(out_of_bounds.status(), StatusCode::BAD_REQUEST);
    assert!(actions.lock().unwrap().is_empty());
}

#[tokio::test]
async fn observations_are_bounded_and_expire() {
    let provider = MockProvider::default();
    let actions = Arc::clone(&provider.actions);
    let app = ComputerRuntime::new_with_observation_limits(
        KEY.as_bytes(),
        provider,
        Duration::from_millis(5),
        1,
    )
    .unwrap()
    .router();
    let (session, generation) = open_session(&app, "exec-a").await;

    let first = observation_id(
        call(
            &app,
            "exec-a",
            "windows-1",
            "computer_list_windows",
            json!({"session_id":session,"expected_session_generation":generation}),
        )
        .await,
    )
    .await;
    let _second = observation_id(
        call(
            &app,
            "exec-a",
            "windows-2",
            "computer_list_windows",
            json!({"session_id":session,"expected_session_generation":generation}),
        )
        .await,
    )
    .await;

    let evicted = call(
        &app,
        "exec-a",
        "shot-evicted",
        "computer_screenshot",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":first,
            "window_id":"wid_fixture",
            "pid":4242,
            "max_width":640,
            "max_height":480
        }),
    )
    .await;
    assert_eq!(evicted.status(), StatusCode::FORBIDDEN);

    let fresh = observation_id(
        call(
            &app,
            "exec-a",
            "windows-3",
            "computer_list_windows",
            json!({"session_id":session,"expected_session_generation":generation}),
        )
        .await,
    )
    .await;
    std::thread::sleep(Duration::from_millis(12));
    let expired = call(
        &app,
        "exec-a",
        "shot-expired",
        "computer_screenshot",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":fresh,
            "window_id":"wid_fixture",
            "pid":4242,
            "max_width":640,
            "max_height":480
        }),
    )
    .await;
    assert_eq!(expired.status(), StatusCode::FORBIDDEN);
    assert!(actions.lock().unwrap().is_empty());
}

#[tokio::test]
async fn closing_session_invalidates_observations_and_actions() {
    let provider = MockProvider::default();
    let actions = Arc::clone(&provider.actions);
    let app = ComputerRuntime::new(KEY.as_bytes(), provider)
        .unwrap()
        .router();
    let (session, generation) = open_session(&app, "exec-a").await;
    let observation = observation_id(
        call(
            &app,
            "exec-a",
            "windows",
            "computer_list_windows",
            json!({"session_id":session,"expected_session_generation":generation}),
        )
        .await,
    )
    .await;

    let close = call(
        &app,
        "exec-a",
        "close",
        "computer_session_close",
        json!({"session_id":session,"expected_session_generation":generation}),
    )
    .await;
    assert_eq!(close.status(), StatusCode::OK);

    let after_close = call(
        &app,
        "exec-a",
        "shot",
        "computer_screenshot",
        json!({
            "session_id":session,
            "expected_session_generation":generation,
            "observation_id":observation,
            "window_id":"wid_fixture",
            "pid":4242,
            "max_width":640,
            "max_height":480
        }),
    )
    .await;
    assert_eq!(after_close.status(), StatusCode::FORBIDDEN);
    assert!(actions.lock().unwrap().is_empty());
}

#[tokio::test]
async fn tampered_body_hash_fails_closed() {
    let app = ComputerRuntime::new(KEY.as_bytes(), MockProvider::default())
        .unwrap()
        .router();
    let body = r#"{"jsonrpc":"2.0","id":"open","method":"tools/call","params":{"name":"computer_session_open","arguments":{}}}"#;
    let mut request = bridge_request(body, KEY, "exec-a");
    request
        .headers_mut()
        .insert("x-lab-body-sha256", "00".repeat(32).parse().unwrap());
    let response = app.oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}

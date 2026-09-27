use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
};

const MAX_READ_BYTES: usize = 512 * 1024;
const MAX_READ_BATCH_FILES: usize = 32;
const MAX_WRITE_BYTES: usize = 512 * 1024;
const CHATGPT_PROJECTS: &str = "/Users/echoman/Documents/ChatGPT";
#[allow(dead_code)]
pub const ANKER_PROJECT_ROOT: &str = "/Users/echoman/Documents/ChatGPT/Anker";

pub const DEFAULT_AUTHORIZED_ROOTS_FILE: &str =
    "/Users/echoman/.local-agent-bridge/authorized-file-roots";

pub fn authorized_roots_file() -> PathBuf {
    std::env::var("LAB_AUTHORIZED_FILE_ROOTS_FILE")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(DEFAULT_AUTHORIZED_ROOTS_FILE))
}

pub fn parse_extra_file_roots(raw: Option<&str>, production: bool) -> Result<Vec<PathBuf>, String> {
    let mut roots = Vec::new();
    if let Some(raw) = raw.map(str::trim).filter(|value| !value.is_empty()) {
        for part in raw.split([',', '\n', ';']) {
            let part = part.trim();
            if part.is_empty() || part.starts_with('#') {
                continue;
            }
            push_unique(&mut roots, validate_extra_file_root(Path::new(part), production)?);
        }
    }
    if roots.len() > 16 {
        return Err("authorized file roots are limited to 16 directories".into());
    }
    Ok(roots)
}

pub fn load_authorized_roots_file(production: bool) -> Result<Vec<PathBuf>, String> {
    load_authorized_roots_from(&authorized_roots_file(), production)
}

pub fn load_authorized_roots_from(path: &Path, production: bool) -> Result<Vec<PathBuf>, String> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let metadata = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(format!("authorized file roots must be a regular file: {}", path.display()));
    }
    let raw = fs::read_to_string(path).map_err(|error| error.to_string())?;
    parse_extra_file_roots(Some(&raw), production)
}

pub fn effective_roots(startup: &[PathBuf], production: bool) -> Vec<PathBuf> {
    let mut roots = startup.to_vec();
    if let Ok(from_file) = load_authorized_roots_file(production) {
        for root in from_file {
            push_unique(&mut roots, root);
        }
    }
    roots
}

fn push_unique(roots: &mut Vec<PathBuf>, root: PathBuf) {
    if !roots.iter().any(|existing| existing == &root) {
        roots.push(root);
    }
}

pub fn validate_extra_file_root(path: &Path, production: bool) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err("extra file root must be an absolute path".into());
    }
    let root = path
        .canonicalize()
        .map_err(|error| format!("extra file root is not readable: {error}"))?;
    if !root.is_dir() {
        return Err(format!("extra file root is not a directory: {}", root.display()));
    }
    let forbidden = [
        Path::new("/"),
        Path::new("/Users"),
        Path::new("/Users/echoman"),
        Path::new("/Users/echoman/Library"),
        Path::new("/Users/echoman/Documents"),
        Path::new(CHATGPT_PROJECTS),
        Path::new("/tmp"),
        Path::new("/private/tmp"),
        Path::new("/etc"),
        Path::new("/var"),
        Path::new("/System"),
        Path::new("/Applications"),
    ];
    if forbidden.iter().any(|path| *path == root.as_path()) {
        return Err("extra file root is too broad or sensitive".into());
    }
    if production {
        let home = Path::new("/Users/echoman");
        if !root.starts_with(home) || root == home {
            return Err("authorized file root must be inside your home directory".into());
        }
        if root.starts_with(Path::new("/Users/echoman/Library")) {
            return Err("authorized file root cannot be under Library".into());
        }
    }
    Ok(root)
}

pub fn is_file_tool(name: &str) -> bool {
    matches!(name, "read" | "search" | "write" | "edit" | "delete")
}

pub fn should_handle(tool_name: &str, arguments: &Value, extra_roots: &[PathBuf]) -> bool {
    if extra_roots.is_empty() {
        return false;
    }
    match tool_name {
        "read" => requested_paths(arguments)
            .iter()
            .any(|path| resolve_existing_or_lexical(path, extra_roots).is_some()),
        "search" => arguments
            .get("path")
            .and_then(Value::as_str)
            .and_then(|path| resolve_existing_or_lexical(path, extra_roots))
            .is_some(),
        "write" | "edit" | "delete" => arguments
            .get("path")
            .and_then(Value::as_str)
            .and_then(|path| resolve_existing_or_lexical(path, extra_roots))
            .is_some(),
        _ => false,
    }
}

pub fn client_instructions(extra_roots: &[PathBuf]) -> Option<String> {
    if extra_roots.is_empty() {
        return None;
    }
    let roots = extra_roots
        .iter()
        .map(|root| root.display().to_string())
        .collect::<Vec<_>>()
        .join(", ");
    Some(format!(
        "Extra project file access is enabled for: {roots}. Use read/write/edit/delete/search with those absolute paths. Do not say the path is outside the workspace. run_command remains limited to the Local Agent Bridge workspace."
    ))
}

pub fn decorate_tool_descriptions(tools: &mut [Value], extra_roots: &[PathBuf]) {
    let Some(instructions) = client_instructions(extra_roots) else {
        return;
    };
    for tool in tools {
        let name = tool.get("name").and_then(Value::as_str).unwrap_or("");
        if !is_file_tool(name) {
            continue;
        }
        let Some(description) = tool.get("description").and_then(Value::as_str) else {
            continue;
        };
        if description.contains("Extra project file access") {
            continue;
        }
        tool["description"] = Value::String(format!("{description} {instructions}"));
    }
}

pub fn handle_call(tool_name: &str, arguments: &Value, extra_roots: &[PathBuf]) -> Result<Value, String> {
    match tool_name {
        "read" => handle_read(arguments, extra_roots),
        "write" => handle_write(arguments, extra_roots),
        "edit" => handle_edit(arguments, extra_roots),
        "delete" => handle_delete(arguments, extra_roots),
        "search" => handle_search(arguments, extra_roots),
        _ => Err(format!("unsupported extra-file tool: {tool_name}")),
    }
}

fn requested_paths(arguments: &Value) -> Vec<String> {
    arguments
        .get("paths")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .filter(|value| !value.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn resolve_existing_or_lexical(path: &str, extra_roots: &[PathBuf]) -> Option<PathBuf> {
    let candidate = PathBuf::from(path);
    if !candidate.is_absolute() {
        return extra_roots.iter().find_map(|root| {
            let joined = root.join(path.trim_start_matches('/'));
            is_under(&joined, root).then_some(joined)
        });
    }
    extra_roots.iter().find_map(|root| {
        let canonical = candidate.canonicalize().unwrap_or_else(|_| candidate.clone());
        is_under(&canonical, root).then_some(canonical)
    })
}

fn resolve_for_write(path: &str, extra_roots: &[PathBuf]) -> Result<PathBuf, String> {
    let candidate = PathBuf::from(path);
    if !candidate.is_absolute() {
        return Err("extra project file path must be absolute".into());
    }
    let parent = candidate.parent().unwrap_or(Path::new("/"));
    let canonical_parent = if parent.exists() {
        parent
            .canonicalize()
            .map_err(|error| format!("parent path is not readable: {error}"))?
    } else {
        lexical_normalize(parent)
    };
    let file_name = candidate
        .file_name()
        .ok_or_else(|| "path must include a file name".to_string())?;
    let resolved = canonical_parent.join(file_name);
    if extra_roots.iter().any(|root| is_under(&resolved, root) || is_under(&canonical_parent, root)) {
        return Ok(resolved);
    }
    Err(format!("Path escapes extra project roots: {}", resolved.display()))
}

fn resolve_existing(path: &str, extra_roots: &[PathBuf]) -> Result<PathBuf, String> {
    let candidate = PathBuf::from(path);
    if !candidate.is_absolute() {
        return Err("extra project file path must be absolute".into());
    }
    let canonical = candidate
        .canonicalize()
        .map_err(|_| format!("File not found: {path}"))?;
    if extra_roots.iter().any(|root| is_under(&canonical, root)) {
        Ok(canonical)
    } else {
        Err(format!("Path escapes extra project roots: {}", canonical.display()))
    }
}

fn is_under(path: &Path, root: &Path) -> bool {
    let path = path.canonicalize().unwrap_or_else(|_| lexical_normalize(path));
    let root = root.canonicalize().unwrap_or_else(|_| lexical_normalize(root));
    path == root || path.starts_with(&root)
}

fn lexical_normalize(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            std::path::Component::ParentDir => {
                out.pop();
            }
            std::path::Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    out
}

fn handle_read(arguments: &Value, extra_roots: &[PathBuf]) -> Result<Value, String> {
    let paths = requested_paths(arguments);
    if paths.is_empty() {
        return Err("paths must contain at least one path".into());
    }
    if paths.len() > MAX_READ_BATCH_FILES {
        return Err(format!(
            "paths must contain at most {MAX_READ_BATCH_FILES} entries"
        ));
    }
    let mut files = Vec::new();
    let mut total_bytes = 0usize;
    let mut total_line_count = 0usize;
    for requested in &paths {
        match resolve_existing(requested, extra_roots) {
            Ok(target) => match read_one(&target) {
                Ok((text, truncated)) => {
                    let bytes = text.len();
                    let line_count = count_lines(&text);
                    total_bytes += bytes;
                    total_line_count += line_count;
                    files.push(json!({
                        "path": target.display().to_string(),
                        "bytes": bytes,
                        "sizeBytes": fs::metadata(&target).map(|meta| meta.len()).unwrap_or(bytes as u64),
                        "lineCount": line_count,
                        "text": text,
                        "truncated": truncated,
                        "budgetTruncated": truncated,
                    }));
                }
                Err(error) => files.push(failed_entry(requested, error)),
            },
            Err(error) => files.push(failed_entry(requested, error)),
        }
    }
    let all_failed = files.iter().all(|file| file.get("error").is_some());
    Ok(json!({
        "toolName": "read",
        "path": files.first().and_then(|file| file.get("path")).cloned().unwrap_or(json!("")),
        "bytes": total_bytes,
        "lineCount": total_line_count,
        "fileCount": files.len(),
        "files": files,
        "allFailed": all_failed,
    }))
}

fn read_one(path: &Path) -> Result<(String, bool), String> {
    if !path.is_file() {
        return Err(format!("Not a file: {}", path.display()));
    }
    let bytes = fs::read(path).map_err(|error| error.to_string())?;
    let mut truncated = false;
    let slice = if bytes.len() > MAX_READ_BYTES {
        truncated = true;
        &bytes[..MAX_READ_BYTES]
    } else {
        &bytes
    };
    let text = String::from_utf8_lossy(slice).into_owned();
    Ok((text, truncated))
}

fn handle_write(arguments: &Value, extra_roots: &[PathBuf]) -> Result<Value, String> {
    let path = required_string(arguments, "path")?;
    let content = required_string(arguments, "content")?;
    if content.len() > MAX_WRITE_BYTES {
        return Err(format!(
            "Content too large: {} bytes (max {MAX_WRITE_BYTES})",
            content.len()
        ));
    }
    let create_dirs = arguments
        .get("create_dirs")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let target = resolve_for_write(&path, extra_roots)?;
    if let Some(parent) = target.parent() {
        if !parent.exists() {
            if create_dirs {
                fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            } else {
                return Err(format!(
                    "Parent directory does not exist: {} (set create_dirs=true)",
                    parent.display()
                ));
            }
        }
    }
    fs::write(&target, content.as_bytes()).map_err(|error| error.to_string())?;
    Ok(json!({
        "toolName": "write",
        "path": target.display().to_string(),
        "bytesWritten": content.len(),
        "createDirs": create_dirs,
        "message": format!("wrote {} bytes to {}", content.len(), target.display()),
    }))
}

fn handle_edit(arguments: &Value, extra_roots: &[PathBuf]) -> Result<Value, String> {
    let path = required_string(arguments, "path")?;
    let target = resolve_existing(&path, extra_roots)?;
    if !target.is_file() {
        return Err(format!("Not a file: {}", target.display()));
    }
    let mut text = fs::read_to_string(&target).map_err(|error| error.to_string())?;
    let edits = arguments
        .get("edits")
        .and_then(Value::as_array)
        .ok_or_else(|| "edits must be an array".to_string())?;
    if edits.is_empty() {
        return Err("edits must contain at least one operation".into());
    }
    let mut replaced = 0usize;
    for edit in edits {
        let old = edit
            .get("old_string")
            .or_else(|| edit.get("old_text"))
            .and_then(Value::as_str)
            .ok_or_else(|| "edit requires old_string".to_string())?;
        let new = edit
            .get("new_string")
            .or_else(|| edit.get("new_text"))
            .and_then(Value::as_str)
            .ok_or_else(|| "edit requires new_string".to_string())?;
        let replace_all = edit
            .get("replace_all")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        if old.is_empty() {
            return Err("old_string must not be empty".into());
        }
        if replace_all {
            let count = text.matches(old).count();
            if count == 0 {
                return Err(format!("old_string not found in {}", target.display()));
            }
            text = text.replace(old, new);
            replaced += count;
        } else {
            let Some(index) = text.find(old) else {
                return Err(format!("old_string not found in {}", target.display()));
            };
            text.replace_range(index..index + old.len(), new);
            replaced += 1;
        }
    }
    if text.len() > MAX_WRITE_BYTES {
        return Err(format!(
            "Edited content too large: {} bytes (max {MAX_WRITE_BYTES})",
            text.len()
        ));
    }
    fs::write(&target, text.as_bytes()).map_err(|error| error.to_string())?;
    Ok(json!({
        "toolName": "edit",
        "path": target.display().to_string(),
        "operationCount": edits.len(),
        "appliedOperations": edits.len(),
        "replacedOccurrences": replaced,
        "bytesWritten": text.len(),
        "message": format!("applied {} edit operation(s) to {}", edits.len(), target.display()),
    }))
}

fn handle_delete(arguments: &Value, extra_roots: &[PathBuf]) -> Result<Value, String> {
    let path = required_string(arguments, "path")?;
    let recursive = arguments
        .get("recursive")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let target = resolve_existing(&path, extra_roots)?;
    let meta = fs::symlink_metadata(&target).map_err(|error| error.to_string())?;
    if extra_roots.iter().any(|root| root == &target) {
        return Err("refusing to delete an extra project root".into());
    }
    if meta.is_dir() {
        if recursive {
            fs::remove_dir_all(&target).map_err(|error| error.to_string())?;
        } else {
            fs::remove_dir(&target).map_err(|error| error.to_string())?;
        }
    } else {
        fs::remove_file(&target).map_err(|error| error.to_string())?;
    }
    Ok(json!({
        "toolName": "delete",
        "path": target.display().to_string(),
        "recursive": recursive,
        "message": format!("deleted {}", target.display()),
    }))
}

fn handle_search(arguments: &Value, extra_roots: &[PathBuf]) -> Result<Value, String> {
    let pattern = required_string(arguments, "pattern")?;
    let path = arguments
        .get("path")
        .and_then(Value::as_str)
        .ok_or_else(|| "search in extra project roots requires an absolute path".to_string())?;
    let root = resolve_existing(path, extra_roots)?;
    let max_matches = arguments
        .get("max_matches")
        .and_then(Value::as_u64)
        .unwrap_or(100)
        .clamp(1, 500) as usize;
    let case_insensitive = arguments
        .get("case_insensitive")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let needle = if case_insensitive {
        pattern.to_ascii_lowercase()
    } else {
        pattern.clone()
    };
    let mut matches = Vec::new();
    search_dir(&root, extra_roots, &needle, case_insensitive, max_matches, &mut matches)?;
    Ok(json!({
        "toolName": "search",
        "path": root.display().to_string(),
        "pattern": pattern,
        "matchCount": matches.len(),
        "matches": matches,
    }))
}

fn search_dir(
    path: &Path,
    _extra_roots: &[PathBuf],
    needle: &str,
    case_insensitive: bool,
    max_matches: usize,
    matches: &mut Vec<Value>,
) -> Result<(), String> {
    if matches.len() >= max_matches {
        return Ok(());
    }
    let meta = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    if meta.file_type().is_symlink() {
        return Ok(());
    }
    if meta.is_dir() {
        for entry in fs::read_dir(path).map_err(|error| error.to_string())? {
            let entry = entry.map_err(|error| error.to_string())?;
            search_dir(&entry.path(), _extra_roots, needle, case_insensitive, max_matches, matches)?;
            if matches.len() >= max_matches {
                break;
            }
        }
        return Ok(());
    }
    if !meta.is_file() || meta.len() > MAX_READ_BYTES as u64 {
        return Ok(());
    }
    let Ok(text) = fs::read_to_string(path) else {
        return Ok(());
    };
    for (index, line) in text.lines().enumerate() {
        let haystack = if case_insensitive {
            line.to_ascii_lowercase()
        } else {
            line.to_string()
        };
        if haystack.contains(needle) {
            matches.push(json!({
                "path": path.display().to_string(),
                "line": index + 1,
                "text": line,
            }));
            if matches.len() >= max_matches {
                break;
            }
        }
    }
    Ok(())
}

fn failed_entry(path: &str, error: String) -> Value {
    json!({
        "path": path,
        "error": error,
        "bytes": 0,
        "sizeBytes": 0,
        "lineCount": 0,
        "text": "",
        "truncated": false,
        "budgetTruncated": false,
    })
}

fn count_lines(text: &str) -> usize {
    if text.is_empty() {
        0
    } else {
        text.lines().count()
    }
}

fn required_string(arguments: &Value, key: &str) -> Result<String, String> {
    arguments
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .ok_or_else(|| format!("{key} must be a non-empty string"))
}

pub fn jsonrpc_result(id: Value, _tool_name: &str, data: Value, is_error: bool) -> Value {
    let text = data
        .get("message")
        .and_then(Value::as_str)
        .map(str::to_string)
        .or_else(|| {
            data.get("files")
                .and_then(Value::as_array)
                .map(|files| {
                    files
                        .iter()
                        .filter_map(|file| file.get("text").and_then(Value::as_str))
                        .collect::<Vec<_>>()
                        .join("\n\n")
                })
        })
        .or_else(|| {
            data.get("matches")
                .and_then(Value::as_array)
                .map(|matches| {
                    matches
                        .iter()
                        .filter_map(|item| item.get("text").and_then(Value::as_str))
                        .collect::<Vec<_>>()
                        .join("\n")
                })
        })
        .unwrap_or_else(|| data.to_string());
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "result": {
            "content": [{"type": "text", "text": text}],
            "structuredContent": data,
            "isError": is_error,
        }
    })
}


#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join("lab-extra-files").join(name);
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        root
    }

    #[test]
    fn production_root_rejects_broad_paths_and_allows_anker() {
        assert!(validate_extra_file_root(Path::new(ANKER_PROJECT_ROOT), true).is_ok());
        assert!(validate_extra_file_root(Path::new(CHATGPT_PROJECTS), true).is_err());
        assert!(validate_extra_file_root(Path::new("/"), true).is_err());
        assert!(validate_extra_file_root(Path::new("/Users/echoman"), true).is_err());
    }

    #[test]
    fn authorized_file_is_merged_into_effective_roots() {
        let root = temp_root("allowlist");
        let file = std::env::temp_dir().join("lab-extra-files").join("allowlist-file");
        fs::write(&file, format!("{}\n", root.display())).unwrap();
        let loaded = load_authorized_roots_from(&file, false).unwrap();
        assert!(loaded.iter().any(|item| item == &root.canonicalize().unwrap()));
    }

    #[test]
    fn read_and_write_stay_inside_extra_root() {
        let root = temp_root("rw");
        let file = root.join("note.html");
        fs::write(&file, "<h1>稳住</h1>").unwrap();
        let roots = vec![root.clone()];
        let read = handle_read(
            &json!({"paths": [file.display().to_string()]}),
            &roots,
        )
        .unwrap();
        assert!(read["files"][0]["text"].as_str().unwrap().contains("稳住"));

        let written = root.join("out.html");
        handle_write(
            &json!({
                "path": written.display().to_string(),
                "content": "ok",
                "create_dirs": false
            }),
            &roots,
        )
        .unwrap();
        assert_eq!(fs::read_to_string(&written).unwrap(), "ok");

        let escaped = handle_write(
            &json!({
                "path": "/tmp/lab-extra-escape.html",
                "content": "nope"
            }),
            &roots,
        );
        assert!(escaped.is_err());
    }

    #[test]
    fn edit_replaces_exact_text() {
        let root = temp_root("edit");
        let file = root.join("doc.html");
        fs::write(&file, "alpha beta alpha").unwrap();
        handle_edit(
            &json!({
                "path": file.display().to_string(),
                "edits": [{"old_string": "beta", "new_string": "gamma"}]
            }),
            &[root],
        )
        .unwrap();
        assert_eq!(fs::read_to_string(&file).unwrap(), "alpha gamma alpha");
    }
}

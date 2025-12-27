use tauri::{Manager, Runtime, Window};
use tauri_plugin_sql::{Migration, MigrationKind};

// 1. Command to toggle Click-Through mode
#[tauri::command]
fn set_ignore_cursor<R: Runtime>(window: Window<R>, ignore: bool) {
    // This allows the user to "lock" the widget (click through it) 
    // or "unlock" it to interact with the AI.
    let _ = window.set_ignore_cursor_events(ignore);
}

// 2. Original greet command (kept for testing)
#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! Your SmartWidget is active.", name)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Define database migrations (creates your task table on first run)
    let migrations = vec![
        Migration {
            version: 1,
            description: "create_tasks_table",
            sql: "CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, content TEXT, status TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);",
            kind: MigrationKind::Up,
        }
    ];

    tauri::Builder::default()
        // Register the SQL plugin with migrations
        .plugin(
            tauri_plugin_sql::Builder::default()
                .add_migrations("sqlite:smart_widget.db", migrations)
                .build(),
        )
        .plugin(tauri_plugin_opener::init())
        // Register the new commands
        .invoke_handler(tauri::generate_handler![
            greet, 
            set_ignore_cursor
        ])
        .setup(|app| {
            // Optional: Ensure the window is transparent and always on top via Rust
            let window = app.get_webview_window("main").unwrap();
            let _ = window.set_always_on_top(true);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
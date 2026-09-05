//! Test-only JSON stdin/stdout bridge. No emitter or enforcement API is exposed.
use std::io::{self, Read};

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("stdin");
    let args: serde_json::Value = serde_json::from_str(&input).expect("JSON");
    let recorded: Vec<serde_json::Value> =
        serde_json::from_value(args["recorded"].clone()).expect("contexts");
    let record = serde_json::from_value(args["record"].clone()).expect("run record");
    let result = agent_hooks::ctk_engine::assert_vector(&args["vector"], &recorded, &record);
    println!("{}", serde_json::to_string(&result).expect("result"));
}

//! Minimal EC2 Query API client for ec2-sim, returning responses as JSON.
//!
//! ec2-sim (moto) does not verify signatures, so requests carry a placeholder
//! SigV4 header whose credential scope names the region.

use serde_json::{Map, Value};
use std::collections::BTreeMap;
use std::time::Duration;

const API_VERSION: &str = "2016-11-15";

fn form_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => out.push(b as char),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

pub async fn call(
    endpoint: &str,
    region: &str,
    action: &str,
    params: &BTreeMap<String, String>,
) -> Result<Value, String> {
    let mut body = format!("Action={}&Version={API_VERSION}", form_encode(action));
    for (k, v) in params {
        body.push('&');
        body.push_str(&form_encode(k));
        body.push('=');
        body.push_str(&form_encode(v));
    }
    let auth = format!(
        "AWS4-HMAC-SHA256 Credential=test/20260101/{region}/ec2/aws4_request, \
         SignedHeaders=host, Signature=0"
    );
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(300))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .post(endpoint)
        .header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
        .header("Authorization", auth)
        .body(body)
        .send()
        .await
        .map_err(|e| format!("cannot reach ec2-sim at {endpoint}: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| e.to_string())?;
    let json = xml_to_json(&text).map_err(|e| {
        format!("{action}: unexpected response (HTTP {status}): {e}: {}", text.chars().take(300).collect::<String>())
    })?;
    if let Some(err) = find_error(&json) {
        return Err(err);
    }
    if !status.is_success() {
        return Err(format!("{action}: HTTP {status}"));
    }
    Ok(json)
}

pub async fn ping(endpoint: &str) -> bool {
    let Ok(client) = reqwest::Client::builder().timeout(Duration::from_millis(800)).build() else {
        return false;
    };
    client.get(endpoint).send().await.is_ok()
}

fn find_error(v: &Value) -> Option<String> {
    let errors = v.get("Errors")?;
    let e = errors.get("Error").unwrap_or(errors);
    let e = if let Value::Array(a) = e { a.first()? } else { e };
    let code = e.get("Code").and_then(Value::as_str).unwrap_or("Error");
    let msg = e.get("Message").and_then(Value::as_str).unwrap_or("");
    Some(format!("{code}: {msg}"))
}

/// Convert an EC2 XML response into JSON:
///   leaf -> string, `<item>` children -> array, empty `...Set` -> [], else object.
pub fn xml_to_json(xml: &str) -> Result<Value, String> {
    let doc = roxmltree::Document::parse(xml).map_err(|e| e.to_string())?;
    Ok(convert(doc.root_element()))
}

fn convert(node: roxmltree::Node) -> Value {
    let children: Vec<_> = node.children().filter(|c| c.is_element()).collect();
    let name = node.tag_name().name();
    if children.is_empty() {
        if name.ends_with("Set") {
            return Value::Array(vec![]);
        }
        return Value::String(node.text().unwrap_or("").trim().to_string());
    }
    if children.iter().all(|c| c.tag_name().name() == "item") {
        return Value::Array(children.into_iter().map(convert).collect());
    }
    let mut map = Map::new();
    for c in children {
        let key = c.tag_name().name().to_string();
        if key == "requestId" || key == "RequestID" {
            continue;
        }
        let val = convert(c);
        match map.get_mut(&key) {
            Some(Value::Array(arr)) => arr.push(val),
            Some(existing) => {
                let first = existing.take();
                *existing = Value::Array(vec![first, val]);
            }
            None => {
                map.insert(key, val);
            }
        }
    }
    Value::Object(map)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_sets_and_errors() {
        let v = xml_to_json(
            "<R><requestId>x</requestId><reservationSet><item><instancesSet><item>\
             <instanceId>i-1</instanceId><tagSet/></item></instancesSet></item></reservationSet></R>",
        )
        .unwrap();
        assert_eq!(v["reservationSet"][0]["instancesSet"][0]["instanceId"], "i-1");
        assert_eq!(v["reservationSet"][0]["instancesSet"][0]["tagSet"], Value::Array(vec![]));
        let e = xml_to_json("<Response><Errors><Error><Code>Bad</Code><Message>nope</Message></Error></Errors></Response>").unwrap();
        assert_eq!(find_error(&e).unwrap(), "Bad: nope");
    }
}

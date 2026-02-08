#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

const REGION = process.env.AWS_REGION || process.env.REGION || "us-east-2";
const PROFILE = process.env.AWS_PROFILE || "";
const STACK_NAME = process.env.STACK_NAME || "tulipbroker-api-qa";
const ORDERS = Number(process.env.ORDER_COUNT || 10);
const CLIENT_ID = process.env.CLIENT_ID || "qa-smoke";
const PERSONAS = ["clusius", "oosterwijck", "leeuwenhoek"];

function runAwsJson(args) {
  const profileArgs = PROFILE ? [`--profile`, PROFILE] : [];
  const out = execFileSync("aws", [...args, ...profileArgs], {
    encoding: "utf8",
  });
  return JSON.parse(out);
}

function getStackOutputs() {
  const data = runAwsJson([
    "cloudformation",
    "describe-stacks",
    "--region",
    REGION,
    "--stack-name",
    STACK_NAME,
  ]);
  const outputs = data.Stacks?.[0]?.Outputs || [];
  const map = Object.fromEntries(outputs.map((o) => [o.OutputKey, o.OutputValue]));
  return map;
}

function randomId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

async function submitOrder(apiBase, payload) {
  const response = await fetch(`${apiBase}/api/orders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const msg = data?.error || `${response.status} ${response.statusText}`;
    throw new Error(`Order failed: ${msg}`);
  }
  if (!data?.orderId) {
    throw new Error("Order accepted but no orderId returned");
  }
  return data.orderId;
}

async function getOrderItem(tableName, orderId) {
  const key = JSON.stringify({ pk: { S: `ORDER#${orderId}` } });
  const data = runAwsJson([
    "dynamodb",
    "get-item",
    "--region",
    REGION,
    "--table-name",
    tableName,
    "--key",
    key,
  ]);
  return data.Item || null;
}

async function waitForItem(tableName, orderId, attempts = 5) {
  for (let i = 0; i < attempts; i += 1) {
    const item = await getOrderItem(tableName, orderId);
    if (item) return item;
    await sleep(750 * (i + 1));
  }
  return null;
}

async function main() {
  const outputs = getStackOutputs();
  const apiBase = outputs.ApiBaseUrl;
  const tableName = outputs.OrdersTableName;
  if (!apiBase || !tableName) {
    throw new Error(`Missing stack outputs (ApiBaseUrl/OrdersTableName) from ${STACK_NAME} in ${REGION}`);
  }

  console.log(`API: ${apiBase}`);
  console.log(`Table: ${tableName}`);
  console.log(`Orders: ${ORDERS}`);

  const orderIds = [];
  for (let i = 0; i < ORDERS; i += 1) {
    const side = i % 2 === 0 ? "BUY" : "SELL";
    const price = 100 + i * 2 + Math.random();
    const quantity = 5 + i;
    const payload = {
      clientId: CLIENT_ID,
      idempotencyKey: randomId(),
      userId: PERSONAS[i % PERSONAS.length],
      side,
      price: Number(price.toFixed(2)),
      quantity,
      timeInForce: "GTC",
    };

    const orderId = await submitOrder(apiBase, payload);
    orderIds.push({ orderId, payload });
    console.log(`Submitted ${i + 1}/${ORDERS}: ${orderId}`);
  }

  let missing = 0;
  for (const { orderId } of orderIds) {
    const item = await waitForItem(tableName, orderId, 6);
    if (!item) {
      missing += 1;
      console.error(`Missing in DynamoDB: ${orderId}`);
    } else {
      console.log(`Verified in DynamoDB: ${orderId}`);
    }
  }

  if (missing > 0) {
    console.error(`\nFAILED: ${missing} orders not found in DynamoDB.`);
    process.exit(1);
  }

  console.log(`\nSUCCESS: All ${ORDERS} orders verified in DynamoDB.`);
}

main().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});

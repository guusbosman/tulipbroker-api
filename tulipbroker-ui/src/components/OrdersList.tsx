import React from "react";

export type OrderListItem = {
  orderId: string;
  userId: string;
  userName: string;
  avatarUrl: string;
  side: string;
  price: number;
  quantity: number;
};

export function OrdersList({ items }: { items: OrderListItem[] }) {
  return (
    <ul>
      {items.map((item) => (
        <li key={item.orderId}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            {item.avatarUrl ? (
              <img
                src={item.avatarUrl}
                alt={`${item.userName} avatar`}
                width={24}
                height={24}
                style={{ borderRadius: "50%" }}
              />
            ) : null}
            <span>
              Submitted by {item.userName} — {item.side} {item.quantity} @ {item.price}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}

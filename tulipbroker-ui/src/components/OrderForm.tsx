import React, { useState } from "react";
import { usePersona } from "../PersonaContext";

export type OrderInput = {
  side: "BUY" | "SELL";
  quantity: number;
  price: number;
  userId: string;
};

export function OrderForm({ onSubmit }: { onSubmit: (order: OrderInput) => void }) {
  const { activePersona } = usePersona();
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [price, setPrice] = useState(1);
  const [quantity, setQuantity] = useState(1);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ side, price, quantity, userId: activePersona.userId });
      }}
    >
      <div>
        <label htmlFor="side-select">Side</label>
        <select
          id="side-select"
          value={side}
          onChange={(event) => setSide(event.target.value as "BUY" | "SELL")}
        >
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
        </select>
      </div>
      <div>
        <label htmlFor="price">Price</label>
        <input
          id="price"
          type="number"
          value={price}
          onChange={(event) => setPrice(Number(event.target.value))}
        />
      </div>
      <div>
        <label htmlFor="quantity">Quantity</label>
        <input
          id="quantity"
          type="number"
          value={quantity}
          onChange={(event) => setQuantity(Number(event.target.value))}
        />
      </div>
      <button type="submit">Submit order as {activePersona.userName}</button>
    </form>
  );
}

import React from "react";
import userEvent from "@testing-library/user-event";
import { screen, waitFor } from "@testing-library/react";
import { PersonaDropdown } from "../PersonaDropdown";
import { OrderForm } from "../OrderForm";
import { OrdersList } from "../OrdersList";
import { renderWithPersona } from "../../test-utils";
import { personas } from "../../personas";

it("allows switching personas from the dropdown", async () => {
  renderWithPersona(<PersonaDropdown />);

  expect(screen.getByLabelText(/active user/i)).toHaveValue(personas[0].userId);
  await userEvent.selectOptions(screen.getByLabelText(/active user/i), personas[1].userId);
  await waitFor(() => {
    expect(screen.getByLabelText(/active user/i)).toHaveValue(personas[1].userId);
  });
});

it("propagates persona selection into the order form submission", async () => {
  const submitted: any[] = [];
  renderWithPersona(
    <>
      <PersonaDropdown />
      <OrderForm onSubmit={(order) => submitted.push(order)} />
    </>
  );

  await userEvent.selectOptions(screen.getByLabelText(/active user/i), personas[2].userId);
  await userEvent.click(screen.getByRole("button", { name: /submit order/i }));

  expect(submitted[0].userId).toBe(personas[2].userId);
});

it("shows the submitting persona in the orders list", () => {
  const items = [
    {
      orderId: "abc",
      userId: personas[0].userId,
      userName: personas[0].userName,
      avatarUrl: personas[0].avatarUrl,
      side: "BUY",
      price: 10,
      quantity: 2,
    },
  ];

  renderWithPersona(<OrdersList items={items} />);

  expect(screen.getByText(/submitted by/i)).toHaveTextContent(personas[0].userName);
  expect(screen.getByAltText(`${personas[0].userName} avatar`)).toBeInTheDocument();
});

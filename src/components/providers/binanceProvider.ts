import axios from "axios";

import { USE_MOCKS } from "../../config/env";

import { getMockPrice } from "../../mocks/mockBinance";

export async function getPrice(symbol: string) {
  if (USE_MOCKS) {
    return getMockPrice(symbol);
  }

  const response = await axios.get(
    `https://api.binance.com/api/v3/ticker/price?symbol=${symbol}`,
  );

  return response.data;
}

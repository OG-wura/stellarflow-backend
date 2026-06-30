import {
  randomFloat,
} from '../utils/random';

export async function getMockPrice(
  symbol: string,
) {
  return {
    symbol,

    price: randomFloat(
      25000,
      70000,
      2,
    ),

    timestamp:
      Date.now(),
  };
}
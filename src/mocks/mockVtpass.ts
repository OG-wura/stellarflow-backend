import {
  randomChoice,
} from '../utils/random';

export async function mockPurchaseAirtime(
  phone: string,
  amount: number,
) {
  return {
    success: true,

    phone,

    amount,

    transactionId:
      crypto.randomUUID(),

    provider: randomChoice([
      'MTN',
      'Airtel',
      'Glo',
      '9mobile',
    ]),

    status: 'successful',
  };
}
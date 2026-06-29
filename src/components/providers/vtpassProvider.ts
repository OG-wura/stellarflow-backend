import axios from 'axios';

import { USE_MOCKS }
  from '../config/env';

import {
  mockPurchaseAirtime,
} from '../mocks/mockVtpass';

export async function purchaseAirtime(
  phone: string,
  amount: number,
) {
  if (USE_MOCKS) {
    return mockPurchaseAirtime(
      phone,
      amount,
    );
  }

  return axios.post(
    process.env.VTPASS_URL!,
    {
      phone,
      amount,
    },
    {
      headers: {
        Authorization:
          process.env
            .VTPASS_API_KEY!,
      },
    },
  );
}
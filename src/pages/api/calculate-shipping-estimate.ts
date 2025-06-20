import type { NextApiRequest, NextApiResponse } from 'next';
import * as z from 'zod';

const shippingRequestSchema = z.object({
  shippingZipCode: z.string().regex(/^\d{5}$/, { message: "Valid 5-digit ZIP required." }),
  lineItems: z.array(z.object({
    quantity: z.number().int().positive(),
    type: z.string(),
  })).optional(),
});

// This is a simplified placeholder for the actual shipping calculation logic.
// In a real-world scenario, this would involve a third-party shipping API.
const calculateShippingCostForOrder = (originZip: string, destZip: string, weightLbs: number): number | null => {
    if (weightLbs <= 0) return 0;
    // Example: $0.50 per pound, with a minimum of $5.
    const cost = Math.max(5.00, weightLbs * 0.50);
    return parseFloat(cost.toFixed(2));
};

export default function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
    if (req.method !== 'POST') {
        res.setHeader('Allow', ['POST']);
        return res.status(405).end(`Method ${req.method} Not Allowed`);
    }

    const validation = shippingRequestSchema.safeParse(req.body);
    if (!validation.success) {
        return res.status(400).json({ message: validation.error.errors[0].message });
    }

    const { shippingZipCode: dest_zip_str, lineItems: line_items = [] } = validation.data;
    const origin_zip = "63366"; // Assuming a fixed origin zip as in the original app

    let total_weight_oz = 0;
    if (line_items.length > 0) {
        total_weight_oz = line_items.reduce((acc: number, item: { quantity: number; type: string; }) => {
            const { quantity, type } = item;
            if (type === 'cross') return acc + (quantity * 5);
            if (type === 'display') return acc + (quantity * 80);
            return acc;
        }, 0);
    }

    if (total_weight_oz <= 0) {
        return res.status(200).json({ estimatedShipping: 0.0 });
    }

    const cost = calculateShippingCostForOrder(origin_zip, dest_zip_str, total_weight_oz / 16.0);

    if (cost !== null) {
        return res.status(200).json({ estimatedShipping: cost });
    } else {
        return res.status(200).json({ estimatedShipping: 0.0, message: "Could not calculate shipping." });
    }
}

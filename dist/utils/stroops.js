import { stroopsFromNumber, stroopsFromString } from "../serialization/helpers.js";
const STROOPS_PER_UNIT = 10_000_000;
/**
 * Convert a price (number or string) to stroops.
 *
 * Optimized to eliminate double type checking by using type-specific
 * conversion paths. Avoids intermediate variables and redundant operations.
 *
 * @performance
 * - Number input: direct multiplication (1-2 operations)
 * - String input: single parseFloat then multiplication
 * - No type checking after initial input dispatch
 */
export function toStroops(price) {
    // Direct dispatch to type-specific converters eliminates intermediate variable
    return typeof price === "string" ? stroopsFromString(price) : stroopsFromNumber(price);
}
//# sourceMappingURL=stroops.js.map
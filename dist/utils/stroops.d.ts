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
export declare function toStroops(price: number | string): number;
//# sourceMappingURL=stroops.d.ts.map
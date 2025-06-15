import math

def get_shipping_zone(origin_zip, dest_zip):
    """
    Estimates the UPS shipping zone based on the absolute difference
    between ZIP codes. This is a simplified heuristic to approximate distance.
    Real UPS zones are based on the origin ZIP code.

    Args:
        origin_zip (int): The origin ZIP code.
        dest_zip (int): The destination ZIP code.

    Returns:
        int: The estimated shipping zone (2-8).
    """
    # This is a simplified model. A larger difference in ZIP codes generally
    # corresponds to a greater distance and a higher zone.
    zip_difference = abs(origin_zip - dest_zip)

    if zip_difference < 150:
        return 2 # Local
    elif zip_difference < 600:
        return 3
    elif zip_difference < 1200:
        return 4
    elif zip_difference < 2000:
        return 5
    elif zip_difference < 3000:
        return 6
    elif zip_difference < 5000:
        return 7
    else:
        return 8 # Cross-country

def get_rate(billable_weight, zone):
    """
    Looks up the shipping rate from a predefined rate chart.

    This data is a sample based on 2024/2025 UPS Ground daily rates for demonstration.
    For official and current rates, always check the UPS website.

    Args:
        billable_weight (int): The weight of the package, rounded up to the
                               next whole pound.
        zone (int): The shipping zone.

    Returns:
        float: The estimated shipping cost, or None if the weight is out of range.
    """
    # A dictionary representing a sample UPS Ground rate chart.
    # Keys are the billable weight in pounds.
    # Values are another dictionary mapping the zone to the cost.
    rate_chart = {
        # Weight: {Zone 2, Zone 3, Zone 4, Zone 5, Zone 6, Zone 7, Zone 8}
        1:  {2: 11, 3: 11, 4: 12, 5: 13, 6: 13, 7: 13, 8: 14},
        2:  {2: 12, 3: 13, 4: 14, 5: 14, 6: 15, 7: 16, 8: 16},
        3:  {2: 12, 3: 14, 4: 15, 5: 15, 6: 16, 7: 17, 8: 18},
        4:  {2: 12, 3: 14, 4: 15, 5: 16, 6: 17, 7: 18, 8: 19},
        5:  {2: 13, 3: 14, 4: 16, 5: 17, 6: 18, 7: 19, 8: 20},
        10: {2: 16, 3: 18, 4: 20, 5: 22, 6: 23, 7: 25, 8: 26},
        15: {2: 19, 3: 21, 4: 24, 5: 27, 6: 30, 7: 33, 8: 35},
        20: {2: 22, 3: 25, 4: 28, 5: 32, 6: 36, 7: 40, 8: 44},
        25: {2: 25, 3: 28, 4: 32, 5: 37, 6: 42, 7: 47, 8: 52},
        30: {2: 27, 3: 31, 4: 35, 5: 42, 6: 48, 7: 54, 8: 60},
        50: {2: 35, 3: 39, 4: 47, 5: 59, 6: 70, 7: 78, 8: 86},
        70: {2: 43, 3: 53, 4: 67, 5: 84, 6: 98, 7: 109, 8: 121},
    }

    if billable_weight > 70:
        print("Weight is over the maximum in this sample rate chart (70 lbs).")
        return None
        
    # Find the closest weight in the chart that is >= the billable_weight
    closest_weight = None
    for weight_key in sorted(rate_chart.keys()):
        if billable_weight <= weight_key:
            closest_weight = weight_key
            break
    
    if closest_weight is None:
        return None

    return rate_chart.get(closest_weight, {}).get(zone)

def estimate_shipping_cost(origin_zip_str, dest_zip_str, weight_str):
    """
    Main function to orchestrate the shipping cost estimation.

    Args:
        origin_zip_str (str): The origin ZIP code as a string.
        dest_zip_str (str): The destination ZIP code as a string.
        weight_str (str): The package weight as a string.
    """
    # --- 1. Input Validation ---
    try:
        origin_zip = int(origin_zip_str)
        dest_zip = int(dest_zip_str)
        if not (10000 <= origin_zip <= 99999 and 10000 <= dest_zip <= 99999):
            raise ValueError("ZIP codes must be 5 digits.")
    except ValueError:
        print("Error: Invalid ZIP code. Please enter a valid 5-digit US ZIP code.")
        return

    try:
        weight = float(weight_str)
        if weight <= 0:
            raise ValueError("Weight must be positive.")
    except ValueError:
        print("Error: Invalid weight. Please enter a positive number.")
        return

    # --- 2. Calculation ---
    # In shipping, any fraction of a pound is rounded up to the next full pound.
    billable_weight = math.ceil(weight)
    
    # Get the estimated zone
    zone = get_shipping_zone(origin_zip, dest_zip)

    # Look up the rate in our chart
    cost = get_rate(billable_weight, zone)

    # --- 3. Display Results ---
    print("\n--- Shipping Cost Estimate ---")
    if cost:
        print(f"  Origin ZIP:      {origin_zip}")
        print(f"  Destination ZIP: {dest_zip}")
        print(f"  Actual Weight:   {weight} lbs")
        print(f"  Billable Weight: {billable_weight} lbs")
        print(f"  Estimated Zone:  {zone}")
        print(f"  Estimated Cost (UPS Ground): ${cost:.2f}")
    else:
        print("Could not calculate a cost. The weight may be too high for this estimator.")
    
    print("\nDisclaimer: This is a simplified estimate and not an official quote.")
    print("Actual prices will vary based on package dimensions, surcharges, and other factors.")

def calculate_shipping_cost_for_order(origin_zip_str, dest_zip_str, weight_lbs_float):
    try:
        origin_zip = int(origin_zip_str)
        dest_zip = int(dest_zip_str)
        if not (10000 <= origin_zip <= 99999 and 10000 <= dest_zip <= 99999):
            # Invalid ZIP format
            return None
    except (ValueError, TypeError):
        # ZIP not convertible to int
        return None

    try:
        # Ensure weight is a positive number
        weight_val = float(weight_lbs_float)
        if weight_val <= 0:
            return None
    except (ValueError, TypeError):
        # Weight not convertible to float or invalid
        return None

    billable_weight = math.ceil(weight_val)
    zone = get_shipping_zone(origin_zip, dest_zip)
    cost = get_rate(billable_weight, zone)
    return cost


if __name__ == "__main__":
    print("UPS Shipping Cost Estimator (for UPS Ground)")
    
    # Get user input
    origin_zip_input = input("Enter the origin ZIP code (e.g., 90210): ")
    dest_zip_input = input("Enter the destination ZIP code (e.g., 10001): ")
    weight_input = input("Enter the package weight in pounds (e.g., 4.5): ")

    # Run the estimator
    estimate_shipping_cost(origin_zip_input, dest_zip_input, weight_input)

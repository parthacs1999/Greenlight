def factorial(n):
    if not isinstance(n, int):
        raise TypeError("Factorial is only defined for integers")
    if n < 0:
        raise ValueError("Factorial is not defined for negative integers")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result

namespace MockCommerce.Models
{
    public class OrderDto
    {
        public int Id { get; set; }
        public string ItemCode { get; set; } = string.Empty;
        public int Quantity { get; set; }
        public decimal UnitPrice { get; set; }
    }
}

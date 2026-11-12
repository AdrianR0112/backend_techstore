IF COL_LENGTH('dbo.Products', 'image_url') IS NULL
BEGIN
    ALTER TABLE dbo.Products ADD image_url nvarchar(500) NULL;
END;
GO

UPDATE dbo.Products
SET image_url = CASE id
    WHEN 1 THEN 'https://images.unsplash.com/photo-1602143407151-7111542de6e8?auto=format&fit=crop&w=800&q=80'
    WHEN 2 THEN 'https://images.unsplash.com/photo-1548839140-29a749e1cf4d?auto=format&fit=crop&w=800&q=80'
    WHEN 3 THEN 'https://images.unsplash.com/photo-1585336261022-680e295ce3fe?auto=format&fit=crop&w=800&q=80'
    WHEN 4 THEN 'https://images.unsplash.com/photo-1455390582262-044cdead277a?auto=format&fit=crop&w=800&q=80'
    ELSE image_url
END
WHERE id IN (1, 2, 3, 4);

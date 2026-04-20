-- Run this once to set up the database before first use

CREATE DATABASE IF NOT EXISTS amazon_reviews CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE amazon_reviews;

CREATE TABLE IF NOT EXISTS raw_reviews (
    review_id   VARCHAR(50)  PRIMARY KEY,
    asin        VARCHAR(20)  NOT NULL,
    product_name VARCHAR(255) NOT NULL,
    category    VARCHAR(100),
    rating      TINYINT,
    title       VARCHAR(500),
    review      TEXT,
    review_date DATE,
    review_url  VARCHAR(500),
    scrape_date DATE,
    INDEX idx_asin       (asin),
    INDEX idx_product    (product_name),
    INDEX idx_scrape_date (scrape_date)
);

CREATE TABLE IF NOT EXISTS review_tags (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    review_id         VARCHAR(50) NOT NULL,
    asin              VARCHAR(20),
    sentiment         VARCHAR(20),
    primary_categories JSON,
    sub_tags          JSON,
    UNIQUE KEY uq_review (review_id)
);

CREATE TABLE IF NOT EXISTS product_ratings_snapshot (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    asin           VARCHAR(20)   NOT NULL,
    product_name   VARCHAR(255),
    scraped_date   DATE          NOT NULL,
    overall_rating DECIMAL(3,1),
    total_ratings  INT,
    UNIQUE KEY uq_asin_date (asin, scraped_date)
);

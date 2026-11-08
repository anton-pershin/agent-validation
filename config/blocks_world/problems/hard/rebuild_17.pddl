(define (problem rebuild-17)
  (:domain blocks-world)
  (:objects a b c d e - block)
  (:init
    (clear a) (clear c) (hand-empty) (on b e) (on c b) (on e d) (on-table a) (on-table d))
  (:goal (and (on a d) (on d e) (on e c) (on-table b) (on-table c)))
)
